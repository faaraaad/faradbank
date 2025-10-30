import random
import json
from decimal import Decimal
from datetime import date, timedelta
from django.utils import timezone
from django.db import transaction
from .models import (
    Profile, InvestmentPlan, Contract, DailyInterestLog, 
    WalletTransaction, CancellationRequest, IntegrationLog
)

class CRMService:
    @staticmethod
    def debit_wallet(user, amount, contract=None, tx_type='INVESTMENT', description=""):
        with transaction.atomic():
            profile = user.profile
            if profile.balance < amount:
                raise ValueError("Insufficient wallet balance.")
            
            profile.balance -= amount
            profile.save()

            tx = WalletTransaction.objects.create(
                user=user,
                contract=contract,
                amount=-amount,
                type=tx_type,
                description=description
            )
            
            # Log integration transfer to CRM
            IntegrationLog.objects.create(
                action='CRM_DEBIT_WALLET',
                contract=contract,
                request_payload=json.dumps({"user": user.username, "amount": float(amount), "type": tx_type}),
                response_payload=json.dumps({"status": "SUCCESS", "new_balance": float(profile.balance), "tx_id": tx.id}),
                status='SUCCESS'
            )
            return tx

    @staticmethod
    def credit_wallet(user, amount, contract=None, tx_type='REFUND', description=""):
        with transaction.atomic():
            profile = user.profile
            profile.balance += amount
            profile.save()

            tx = WalletTransaction.objects.create(
                user=user,
                contract=contract,
                amount=amount,
                type=tx_type,
                description=description
            )
            
            # Log integration transfer to CRM
            IntegrationLog.objects.create(
                action='CRM_CREDIT_WALLET',
                contract=contract,
                request_payload=json.dumps({"user": user.username, "amount": float(amount), "type": tx_type}),
                response_payload=json.dumps({"status": "SUCCESS", "new_balance": float(profile.balance), "tx_id": tx.id}),
                status='SUCCESS'
            )
            return tx

