from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework import viewsets, status, permissions
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django.db import transaction
from django.db.models import Sum, Count
from django.utils import timezone
from decimal import Decimal
from datetime import date, timedelta
import calendar

from .models import (
    Profile, InvestmentPlan, Contract, DailyInterestLog, 
    WalletTransaction, CancellationRequest, IntegrationLog, SimulationState
)
from .serializers import (
    UserSerializer, InvestmentPlanSerializer, ContractSerializer, 
    WalletTransactionSerializer, CancellationRequestSerializer, IntegrationLogSerializer
)
from .services import CRMService, MT5Service, InterestEngineService


def get_current_virtual_date():
    state, created = SimulationState.objects.get_or_create(id=1)
    return state.virtual_date


class AuthLoginView(APIView):
    def post(self, request):
        username = request.data.get('username')
        role = request.data.get('role', 'INVESTOR') # INVESTOR or MANAGER
        
        if not username:
            return Response({"error": "Username is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # In a local development demo, let's create the user if it doesn't exist
        user, created = User.objects.get_or_create(username=username)
        if created:
            user.set_password('password123')
            user.email = f"{username}@faradbank.com"
            user.save()
            
            # Setup role
            user.profile.role = role
            user.profile.balance = Decimal('5000.00') # Seed initial 5000 USDT for testing!
            user.profile.save()
        else:
            if role != user.profile.role:
                user.profile.role = role
                user.profile.save()
                
        serializer = UserSerializer(user)
        return Response({
            "token": "dummy-jwt-token-for-demo",
            "user": serializer.data
        })


class WalletView(APIView):
    def get(self, request):
        username = request.query_params.get('username')
        if not username:
            return Response({"error": "Username required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
            
        serializer = UserSerializer(user)
        return Response(serializer.data['profile'])

    def post(self, request):
        # Allow topping up the wallet to simulate CRM inputs
        username = request.data.get('username')
        amount = request.data.get('amount')
        
        if not username or not amount:
            return Response({"error": "Username and amount are required"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            user = User.objects.get(username=username)
            amount_dec = Decimal(str(amount))
        except (User.DoesNotExist, ValueError):
            return Response({"error": "Invalid user or amount"}, status=status.HTTP_400_BAD_REQUEST)
            
        CRMService.credit_wallet(user, amount_dec, tx_type='DEPOSIT', description="CRM Wallet External Topup")
        serializer = UserSerializer(user)
        return Response(serializer.data['profile'])


class TransactionHistoryView(APIView):
    def get(self, request):
        username = request.query_params.get('username')
        if not username:
            return Response({"error": "Username required"}, status=status.HTTP_400_BAD_REQUEST)
            
        txs = WalletTransaction.objects.filter(user__username=username).order_by('-created_at')
        serializer = WalletTransactionSerializer(txs, many=True)
        return Response(serializer.data)


class InvestmentPlanViewSet(viewsets.ModelViewSet):
    queryset = InvestmentPlan.objects.all().order_by('duration_months')
    serializer_class = InvestmentPlanSerializer

    def update(self, request, *pk_dict, **kwargs):
        # Admin can update plan interest rate or minimum deposit
        instance = self.get_object()
        
        rate = request.data.get('interest_rate_apy')
        min_dep = request.data.get('min_deposit')
        
        if rate is not None:
            instance.interest_rate_apy = Decimal(str(rate))
        if min_dep is not None:
            instance.min_deposit = Decimal(str(min_dep))
            
        instance.save()
        return Response(self.get_serializer(instance).data)


class ContractViewSet(viewsets.ModelViewSet):
    serializer_class = ContractSerializer

    def get_queryset(self):
        username = self.request.query_params.get('username')
        virtual_date = get_current_virtual_date()
        
        # Provide current virtual date in context for progress calculations
        self.context = {'virtual_date': virtual_date}
        
        if username:
            return Contract.objects.filter(user__username=username).order_by('-created_at')
        return Contract.objects.all().order_by('-created_at')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['virtual_date'] = get_current_virtual_date()
        return context

    def create(self, request, *args, **kwargs):
        username = request.data.get('username')
        plan_id = request.data.get('plan')
        principal = request.data.get('principal')
        auto_renew = request.data.get('auto_renew', False)

        if not username or not plan_id or not principal:
            return Response({"error": "username, plan, and principal are required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(username=username)
            plan = InvestmentPlan.objects.get(id=plan_id)
            principal_dec = Decimal(str(principal))
        except (User.DoesNotExist, InvestmentPlan.DoesNotExist, ValueError):
            return Response({"error": "Invalid user, plan, or principal"}, status=status.HTTP_400_BAD_REQUEST)

        if principal_dec < plan.min_deposit:
            return Response({"error": f"Minimum deposit for this plan is {plan.min_deposit} USDT"}, status=status.HTTP_400_BAD_REQUEST)

        if user.profile.balance < principal_dec:
            return Response({"error": "Insufficient CRM wallet balance"}, status=status.HTTP_400_BAD_REQUEST)

        virtual_date = get_current_virtual_date()
        # Compute maturity date
        # 1 month is approx 30 days, or we can add precise months
        days_to_add = plan.duration_months * 30
        maturity_date = virtual_date + timedelta(days=days_to_add)

        with transaction.atomic():
            # Create Contract
            contract = Contract.objects.create(
                user=user,
                plan=plan,
                principal=principal_dec,
                interest_rate_apy=plan.interest_rate_apy,
                status='ACTIVE',
                start_date=virtual_date,
                maturity_date=maturity_date,
                auto_renew=auto_renew
            )

            # Block wallet in CRM
            CRMService.debit_wallet(
                user=user,
                amount=principal_dec,
                contract=contract,
                tx_type='INVESTMENT',
                description=f"Locked liquidity into {plan.name} (Contract #{contract.id})"
            )

            # Create MT5 read-only account
            MT5Service.create_readonly_account(contract)

            # Push balance to MT5 account
            MT5Service.lock_balance(contract)

        serializer = self.get_serializer(contract)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post'], url_path='cancel')
    def cancel_contract(self, request, pk=None):
        contract = self.get_object()
        
        if contract.status != 'ACTIVE':
            return Response({"error": "Only active contracts can be cancelled."}, status=status.HTTP_400_BAD_REQUEST)
            
        if not contract.plan.is_cancellable:
            return Response({"error": "1-Month Time Deposits are strictly non-cancellable."}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Calculate invoice preview details
        invoice = InterestEngineService.calculate_cancellation_invoice(contract)

        if request.method == 'GET':
            return Response(invoice)

        # 2. Process POST (submit request)
        virtual_date = get_current_virtual_date()
        # Scheduled refund is paid between the 3rd and 5th of the FOLLOWING month
        # So it's 3rd of next month
        if virtual_date.month == 12:
            refund_month = 1
            refund_year = virtual_date.year + 1
        else:
            refund_month = virtual_date.month + 1
            refund_year = virtual_date.year
            
        refund_date = date(refund_year, refund_month, 3)

        with transaction.atomic():
            contract.status = 'PENDING_CANCELLATION'
            contract.save()

            cancel_req = CancellationRequest.objects.create(
                contract=contract,
                requested_by=request.user if request.user.is_authenticated else contract.user,
                penalty_amount=Decimal(str(invoice['penalty'])),
                clawback_interest_amount=Decimal(str(invoice['clawback'])),
                estimated_refund_amount=Decimal(str(invoice['refund'])),
                refund_date=refund_date
            )

        return Response({
            "message": "Cancellation request submitted successfully. Awaiting Manager approval.",
            "request_details": CancellationRequestSerializer(cancel_req).data
        })

    @action(detail=True, methods=['post'], url_path='upgrade')
    def upgrade_contract(self, request, pk=None):
        contract = self.get_object()
        new_plan_id = request.data.get('plan_id')

        if contract.status != 'ACTIVE':
            return Response({"error": "Only active contracts can be upgraded."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            new_plan = InvestmentPlan.objects.get(id=new_plan_id)
        except InvestmentPlan.DoesNotExist:
            return Response({"error": "Plan not found"}, status=status.HTTP_404_NOT_FOUND)

        # Permitted only toward longer-term plans
        if new_plan.duration_months <= contract.plan.duration_months:
            return Response({"error": "Upgrades are only permitted to longer-term plans."}, status=status.HTTP_400_BAD_REQUEST)

        # Calculate extension
        month_difference = new_plan.duration_months - contract.plan.duration_months
        days_to_add = month_difference * 30
        
        with transaction.atomic():
            old_plan_name = contract.plan.name
            contract.plan = new_plan
            contract.maturity_date = contract.maturity_date + timedelta(days=days_to_add)
            
            # The rate will change from the next calendar month.
            # We record it in pending_upgrade_apy
            contract.pending_upgrade_apy = new_plan.interest_rate_apy
            contract.save()

            # Audit CRM Integration Log / Log change
            IntegrationLog.objects.create(
                action='PLAN_UPGRADE',
                contract=contract,
                request_payload=json.dumps({"contract_id": contract.id, "from_plan": old_plan_name, "to_plan": new_plan.name}),
                response_payload=json.dumps({"status": "PENDING_RATE_CHANGE", "new_rate_active_date": "1st of next month"}),
                status='SUCCESS'
            )

        return Response({
            "message": f"Successfully upgraded contract #{contract.id} to {new_plan.name}. The new APY will activate from the next calendar month.",
            "contract": ContractSerializer(contract, context={'virtual_date': get_current_virtual_date()}).data
        })

    @action(detail=True, methods=['post'], url_path='toggle-renewal')
    def toggle_renewal(self, request, pk=None):
        contract = self.get_object()
        contract.auto_renew = not contract.auto_renew
        contract.save()
        return Response({
            "auto_renew": contract.auto_renew,
            "message": f"Auto-renewal is now {'enabled' if contract.auto_renew else 'disabled'}."
        })


class InvestorDashboardOverview(APIView):
    def get(self, request):
        username = request.query_params.get('username')
        if not username:
            return Response({"error": "Username required"}, status=status.HTTP_400_BAD_REQUEST)

        virtual_date = get_current_virtual_date()

        # 1. Total Assets (Active and Pending cancellation principal)
        active_contracts = Contract.objects.filter(user__username=username, status__in=['ACTIVE', 'PENDING_CANCELLATION'])
        total_assets = active_contracts.aggregate(total=Sum('principal'))['total'] or Decimal('0.00')

        # 2. Total Interest Received (Sum of paid daily logs or INTEREST_PAYOUT txs)
        payout_txs = WalletTransaction.objects.filter(user__username=username, type='INTEREST_PAYOUT')
        total_received = payout_txs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        # 3. Estimated Interest for the Next Month
        # Sum of: (Principal * APY / 100 / 12) for all active contracts next month
        # We also check if the active contract has a pending upgrade.
        estimated_next_month = Decimal('0.00')
        for contract in Contract.objects.filter(user__username=username, status='ACTIVE'):
            # If there's an upgrade pending, the rate will flip to new APY next month
            rate = contract.pending_upgrade_apy if contract.pending_upgrade_apy else contract.interest_rate_apy
            monthly_interest = contract.principal * (rate / Decimal('100.00') / Decimal('12.00'))
            estimated_next_month += monthly_interest

        estimated_next_month = estimated_next_month.quantize(Decimal('0.00'))

        return Response({
            "total_assets": float(total_assets),
            "total_interest_received": float(total_received),
            "estimated_next_month": float(estimated_next_month),
            "virtual_date": virtual_date.strftime('%Y-%m-%d')
        })


class BackOfficeAdminView(APIView):
    def get(self, request):
        # 1. Total liquidity (Total AUM)
        active_contracts = Contract.objects.filter(status__in=['ACTIVE', 'PENDING_CANCELLATION'])
        total_aum = active_contracts.aggregate(total=Sum('principal'))['total'] or Decimal('0.00')

        # 2. Total Active Users (Users with at least one active/pending contract)
        active_users = Contract.objects.filter(status__in=['ACTIVE', 'PENDING_CANCELLATION']).values('user').distinct().count()

        # 3. Breakdown per plan
        plan_breakdown = []
        plans = InvestmentPlan.objects.all()
        for p in plans:
            count = Contract.objects.filter(plan=p, status__in=['ACTIVE', 'PENDING_CANCELLATION']).count()
            amt = Contract.objects.filter(plan=p, status__in=['ACTIVE', 'PENDING_CANCELLATION']).aggregate(total=Sum('principal'))['total'] or Decimal('0.00')
            plan_breakdown.append({
                "plan_id": p.id,
                "plan_name": p.name,
                "apy": float(p.interest_rate_apy),
                "count": count,
                "amount": float(amt)
            })

        return Response({
            "total_aum": float(total_aum),
            "active_users": active_users,
            "plan_breakdown": plan_breakdown
        })


class BackOfficeCancellationsView(APIView):
    def get(self, request):
        reqs = CancellationRequest.objects.filter(status='PENDING').order_by('-requested_at')
        serializer = CancellationRequestSerializer(reqs, many=True)
        return Response(serializer.data)

    def post(self, request):
        req_id = request.data.get('request_id')
        action = request.data.get('action') # APPROVE or REJECT
        rejection_reason = request.data.get('rejection_reason', '')

        if not req_id or not action:
            return Response({"error": "request_id and action are required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cancel_req = CancellationRequest.objects.get(id=req_id)
        except CancellationRequest.DoesNotExist:
            return Response({"error": "Cancellation request not found"}, status=status.HTTP_404_NOT_FOUND)

        if cancel_req.status != 'PENDING':
            return Response({"error": "Request has already been processed"}, status=status.HTTP_400_BAD_REQUEST)

        contract = cancel_req.contract

        if action == 'APPROVE':
            with transaction.atomic():
                cancel_req.status = 'APPROVED'
                cancel_req.resolved_at = timezone.now()
                # Scheduled refund remains at the 3rd of the following month (already set in create)
                cancel_req.save()
                
                # Keep contract in PENDING_CANCELLATION status. Refund cron will unlock and refund on schedule
                
            return Response({
                "message": "Cancellation request approved. Principal release is scheduled for the next monthly settlement cycle (3rd-5th).",
                "request": CancellationRequestSerializer(cancel_req).data
            })
        
        elif action == 'REJECT':
            with transaction.atomic():
                cancel_req.status = 'REJECTED'
                cancel_req.rejection_reason = rejection_reason
                cancel_req.resolved_at = timezone.now()
                cancel_req.save()

                # Revert contract back to ACTIVE
                contract.status = 'ACTIVE'
                contract.save()

            return Response({
                "message": "Cancellation request rejected. Contract has been restored to active status.",
                "request": CancellationRequestSerializer(cancel_req).data
            })

        return Response({"error": "Invalid action"}, status=status.HTTP_400_BAD_REQUEST)


class BackOfficeFinancialReportingView(APIView):
    def get(self, request):
        virtual_date = get_current_virtual_date()
        
        # 1. Total payable interest for the previous month
        # (unpaid DailyInterestLog for previous month)
        prev_month = 12 if virtual_date.month == 1 else virtual_date.month - 1
        prev_year = virtual_date.year - 1 if virtual_date.month == 1 else virtual_date.year

        unpaid_amount = DailyInterestLog.objects.filter(
            date__year=prev_year,
            date__month=prev_month,
            is_paid=False
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        # 2. Contracts reaching maturity in the following calendar month
        # e.g., if virtual_date is May 19, following month is June
        next_month = 1 if virtual_date.month == 12 else virtual_date.month + 1
        next_year = virtual_date.year + 1 if virtual_date.month == 12 else virtual_date.year

        maturing_contracts = Contract.objects.filter(
            maturity_date__year=next_year,
            maturity_date__month=next_month,
            status='ACTIVE'
        )
        maturing_serializer = ContractSerializer(maturing_contracts, many=True, context={'virtual_date': virtual_date})

        return Response({
            "previous_month_payable_interest": {
                "year": prev_year,
                "month": prev_month,
                "month_name": calendar.month_name[prev_month],
                "payable_amount": float(unpaid_amount)
            },
            "maturing_contracts_next_month": maturing_serializer.data
        })


class BackOfficeUserAuditView(APIView):
    def get(self, request):
        users = User.objects.filter(profile__role='INVESTOR').order_by('username')
        audit_list = []
        for u in users:
            contracts = Contract.objects.filter(user=u).order_by('-created_at')
            plan_changes = IntegrationLog.objects.filter(contract__user=u, action='PLAN_UPGRADE').order_by('-created_at')
            
            audit_list.append({
                "username": u.username,
                "email": u.email,
                "balance": float(u.profile.balance),
                "contracts_count": contracts.count(),
                "contracts": ContractSerializer(contracts, many=True, context={'virtual_date': get_current_virtual_date()}).data,
                "plan_changes_log": IntegrationLogSerializer(plan_changes, many=True).data
            })
        return Response(audit_list)


class IntegrationLogListView(APIView):
    def get(self, request):
        logs = IntegrationLog.objects.all().order_by('-created_at')[:100] # Cap at last 100
        serializer = IntegrationLogSerializer(logs, many=True)
        return Response({
            "mt5_connection_status": MT5Service.get_ping_status(),
            "logs": serializer.data
        })


class SimulationStateView(APIView):
    def get(self, request):
        return Response({
            "virtual_date": get_current_virtual_date().strftime('%Y-%m-%d')
        })


class SimulationTimeTravelView(APIView):
    def post(self, request):
        days = request.data.get('days')
        months = request.data.get('months')
        
        if days is None and months is None:
            return Response({"error": "Provide either days or months to fast-forward"}, status=status.HTTP_400_BAD_REQUEST)
            
        current_date = get_current_virtual_date()
        target_date = current_date
        
        if days:
            target_date += timedelta(days=int(days))
        elif months:
            # Advance months (approx 30 days per month)
            target_date += timedelta(days=int(months) * 30)

        logs_created = 0
        payouts_info = []
        refunds_info = []
        date_cursor = current_date

        # Loop through each day in the transition sequence to execute daily interest,
        # roll rate upgrades, and trigger monthly payouts & cancellation refunds
        while date_cursor < target_date:
            date_cursor += timedelta(days=1)
            
            # 1. Flip pending plan upgrades on the 1st of the month
            if date_cursor.day == 1:
                upgraded_contracts = Contract.objects.filter(pending_upgrade_apy__isnull=False)
                for contract in upgraded_contracts:
                    contract.interest_rate_apy = contract.pending_upgrade_apy
                    contract.pending_upgrade_apy = None
                    contract.save()

            # 2. Run nightly daily interest calculation
            daily_logs = InterestEngineService.calculate_daily_interest(date_cursor)
            logs_created += daily_logs

            # 3. Monthly Interest Settlement payout (runs automatically between the 3rd and 5th of each calendar month)
            # Let's run it precisely on the 3rd day of the month for the previous month!
            if date_cursor.day == 3:
                prev_month = 12 if date_cursor.month == 1 else date_cursor.month - 1
                prev_year = date_cursor.year - 1 if date_cursor.month == 1 else date_cursor.year
                
                payout = InterestEngineService.process_monthly_payout(prev_year, prev_month)
                if payout['payouts_completed'] > 0:
                    payouts_info.append({
                        "date": date_cursor.strftime('%Y-%m-%d'),
                        "for_period": f"{prev_month:02d}/{prev_year}",
                        **payout
                    })

            # 4. Refund processed early-cancellations (scheduled on the 3rd day of next month)
            # Let's trigger refund processing between the 3rd and 5th of the month
            if date_cursor.day == 3:
                refund = InterestEngineService.process_scheduled_refunds(date_cursor)
                if refund['refunds_processed'] > 0:
                    refunds_info.append({
                        "date": date_cursor.strftime('%Y-%m-%d'),
                        **refund
                    })

        # Save the new virtual date
        state = SimulationState.objects.get(id=1)
        state.virtual_date = target_date
        state.save()

        return Response({
            "virtual_date_before": current_date.strftime('%Y-%m-%d'),
            "virtual_date_after": target_date.strftime('%Y-%m-%d'),
            "total_days_advanced": (target_date - current_date).days,
            "daily_interest_logs_created": logs_created,
            "interest_payouts_executed": payouts_info,
            "cancellation_refunds_processed": refunds_info
        })

# End of File - FaradBank Fixed-Yield Investment Engine
