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

