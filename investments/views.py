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


