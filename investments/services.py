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


class MT5Service:
    @staticmethod
    def create_readonly_account(contract):
        # Simulates creating a Read-Only MT5 account for this investment contract
        login = random.randint(5000000, 9999999)
        ro_password = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789", k=10))
        group = f"FaradBank_ReadOnly_{contract.plan.id.upper()}"
        
        request_data = {
            "Action": "AccountCreate",
            "Name": f"{contract.user.username}_c{contract.id}",
            "Group": group,
            "Leverage": "1:1",
            "ReadOnly": True,
            "Comment": f"Blocked contract sub-account for {contract.principal} USDT"
        }
        
        response_data = {
            "RetCode": "0 Done",
            "Login": login,
            "PasswordReadOnly": ro_password,
            "Server": "FaradBank-Live",
            "Time": timezone.now().strftime('%Y.%m.%d %H:%M:%S')
        }
        
        IntegrationLog.objects.create(
            action='MT5_CREATE_ACCOUNT',
            contract=contract,
            request_payload=json.dumps(request_data, indent=2),
            response_payload=json.dumps(response_data, indent=2),
            status='SUCCESS'
        )
        
        contract.mt5_account_id = str(login)
        contract.save()
        
        return login, ro_password

