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
