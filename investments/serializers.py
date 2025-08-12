from rest_framework import serializers
from django.contrib.auth.models import User
from .models import (
    Profile, InvestmentPlan, Contract, DailyInterestLog, 
    WalletTransaction, CancellationRequest, IntegrationLog
)
from decimal import Decimal
from datetime import date

class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ['balance', 'role']

class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'profile']


class InvestmentPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvestmentPlan
        fields = ['id', 'name', 'duration_months', 'interest_rate_apy', 'min_deposit', 'is_cancellable', 'cancellation_penalty_pct']


