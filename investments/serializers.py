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


class ContractSerializer(serializers.ModelSerializer):
    plan_details = InvestmentPlanSerializer(source='plan', read_only=True)
    earned_interest_to_date = serializers.SerializerMethodField()
    progress_percent = serializers.SerializerMethodField()
    remaining_days = serializers.SerializerMethodField()
    
    class Meta:
        model = Contract
        fields = [
            'id', 'user', 'plan', 'plan_details', 'principal', 
            'interest_rate_apy', 'status', 'start_date', 'maturity_date', 
            'auto_renew', 'mt5_account_id', 'created_at', 
            'earned_interest_to_date', 'progress_percent', 'remaining_days'
        ]
        read_only_fields = ['user', 'interest_rate_apy', 'status', 'start_date', 'maturity_date', 'mt5_account_id']

    def get_earned_interest_to_date(self, obj):
        # Calculates sum of all daily interest logs to date (whether paid or unpaid)
        logs = obj.daily_interest_logs.all()
        total = sum(log.amount for log in logs)
        return float(total)

    def get_progress_percent(self, obj):
        # We need a progress percent from start_date to maturity_date based on current virtual date.
        # Since we use simulation time-travel, we will fetch the current system date from query params or context, 
        # or fall back to the timezone/local date.
        # Let's read standard request date if it exists in serializer context.
        request_date = self.context.get('virtual_date', date.today())
        
        if obj.status == 'CANCELLED':
            return 100
        if obj.status == 'MATURED':
            return 100
            
        total_days = (obj.maturity_date - obj.start_date).days
        elapsed_days = (request_date - obj.start_date).days
        
        if total_days <= 0:
            return 100
        if elapsed_days <= 0:
            return 0
        if elapsed_days >= total_days:
            return 100
            
        return round((elapsed_days / total_days) * 100, 2)

    def get_remaining_days(self, obj):
        request_date = self.context.get('virtual_date', date.today())
        if obj.status in ['CANCELLED', 'MATURED']:
            return 0
        days = (obj.maturity_date - request_date).days
        return max(0, days)


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = ['id', 'user', 'contract', 'amount', 'type', 'description', 'created_at']




class CancellationRequestSerializer(serializers.ModelSerializer):
    contract_details = ContractSerializer(source='contract', read_only=True)
    username = serializers.CharField(source='contract.user.username', read_only=True)

    class Meta:
        model = CancellationRequest
        fields = [
            'id', 'contract', 'contract_details', 'username', 'requested_by', 
            'requested_at', 'status', 'penalty_amount', 'clawback_interest_amount', 
            'estimated_refund_amount', 'refund_date', 'resolved_by', 'resolved_at', 
            'rejection_reason'
        ]
        read_only_fields = ['requested_by', 'requested_at', 'status', 'penalty_amount', 
                           'clawback_interest_amount', 'estimated_refund_amount', 'refund_date', 
                           'resolved_by', 'resolved_at']




class IntegrationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntegrationLog
        fields = ['id', 'action', 'contract', 'request_payload', 'response_payload', 'status', 'created_at']
