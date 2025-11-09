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

    @staticmethod
    def lock_balance(contract):
        # Simulates pushing/locking the balance inside the MT5 sub-account
        request_data = {
            "Action": "BalanceLock",
            "Login": contract.mt5_account_id,
            "Amount": float(contract.principal),
            "Currency": "USDT",
            "Type": "CreditBlock"
        }
        
        response_data = {
            "RetCode": "0 Done",
            "TransactionID": random.randint(100000, 999999),
            "BalanceBlocked": float(contract.principal),
            "MT5Status": "Blocked"
        }
        
        IntegrationLog.objects.create(
            action='MT5_LOCK_BALANCE',
            contract=contract,
            request_payload=json.dumps(request_data, indent=2),
            response_payload=json.dumps(response_data, indent=2),
            status='SUCCESS'
        )
        return True

    @staticmethod
    def unlock_balance(contract):
        # Simulates releasing the balance from the MT5 sub-account upon cancellation/maturity
        request_data = {
            "Action": "BalanceUnlock",
            "Login": contract.mt5_account_id,
            "Amount": float(contract.principal),
            "Currency": "USDT"
        }
        
        response_data = {
            "RetCode": "0 Done",
            "TransactionID": random.randint(100000, 999999),
            "BalanceBlocked": 0.0,
            "MT5Status": "Active"
        }
        
        IntegrationLog.objects.create(
            action='MT5_UNLOCK_BALANCE',
            contract=contract,
            request_payload=json.dumps(request_data, indent=2),
            response_payload=json.dumps(response_data, indent=2),
            status='SUCCESS'
        )
        return True

    @staticmethod
    def get_ping_status():
        # Health status ping to display live connection dashboard metrics
        ping_latency = round(random.uniform(5.5, 25.2), 2)
        connected = True
        return {
            "connected": connected,
            "latency_ms": ping_latency,
            "broker": "MetaQuotes Software Corp.",
            "server": "FaradBank-Live",
            "active_sockets": random.randint(10, 45)
        }


