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


class InterestEngineService:
    @staticmethod
    def calculate_daily_interest(target_date):
        """
        Calculates daily interest for all ACTIVE contracts.
        Daily Interest = (Principal * (APY / 100)) / 365
        """
        active_contracts = Contract.objects.filter(status='ACTIVE')
        logs_created = 0

        for contract in active_contracts:
            # Check if plan upgrade will change rate from this month
            # Interest rate is locked inside contract, but if there was an upgrade,
            # we check if a plan change applies starting the 1st of the following month.
            # (Upgrade logic is handled by changing contract.interest_rate_apy when month flips)
            
            # Check if this date already has an interest log for this contract
            if DailyInterestLog.objects.filter(contract=contract, date=target_date).exists():
                continue
                
            # If contract is already past maturity, handle automatic renewal or mature it
            if target_date > contract.maturity_date:
                if contract.auto_renew:
                    # Auto-renew principal
                    with transaction.atomic():
                        old_principal = contract.principal
                        old_plan = contract.plan
                        
                        # Mature old contract
                        contract.status = 'MATURED'
                        contract.save()
                        
                        # Create wallet transactions for auditability
                        # Re-deposit principal and re-lock
                        CRMService.credit_wallet(contract.user, old_principal, contract, 'REFUND', f"Maturity Refund for Contract #{contract.id}")
                        
                        # Start new contract
                        new_maturity = target_date + timedelta(days=old_plan.duration_months * 30) # rough estimate
                        new_contract = Contract.objects.create(
                            user=contract.user,
                            plan=old_plan,
                            principal=old_principal,
                            interest_rate_apy=old_plan.interest_rate_apy,
                            status='ACTIVE',
                            start_date=target_date,
                            maturity_date=new_maturity,
                            auto_renew=True
                        )
                        MT5Service.create_readonly_account(new_contract)
                        MT5Service.lock_balance(new_contract)
                        CRMService.debit_wallet(contract.user, old_principal, new_contract, 'INVESTMENT', f"Auto-Renewal Lock for Contract #{new_contract.id}")
                    continue
                else:
                    # Mark contract as matured, release principal
                    with transaction.atomic():
                        contract.status = 'MATURED'
                        contract.save()
                        MT5Service.unlock_balance(contract)
                        CRMService.credit_wallet(contract.user, contract.principal, contract, 'REFUND', f"Maturity Refund for Contract #{contract.id}")
                    continue

            # Daily rate is APY / 100 / 365
            apy = contract.interest_rate_apy
            daily_rate = apy / Decimal('100.00') / Decimal('365.00')
            daily_interest = contract.principal * daily_rate
            
            # Precision rounding
            daily_interest = daily_interest.quantize(Decimal('0.000001'))

            DailyInterestLog.objects.create(
                contract=contract,
                date=target_date,
                amount=daily_interest
            )
            logs_created += 1

        return logs_created

    @staticmethod
    def process_monthly_payout(year, month):
        """
        Aggregates daily interest logs from the previous month and pays them into the wallet.
        This payout runs between the 3rd and 5th of each month.
        """
        unpaid_logs = DailyInterestLog.objects.filter(
            date__year=year,
            date__month=month,
            is_paid=False
        )

        # Group by contract
        contract_earnings = {}
        for log in unpaid_logs:
            contract_earnings[log.contract] = contract_earnings.get(log.contract, Decimal('0.000000')) + log.amount

        payouts_completed = 0
        total_payout_amount = Decimal('0.00')

        with transaction.atomic():
            for contract, total_interest in contract_earnings.items():
                # Round to 2 decimal places for wallet credit
                rounded_interest = total_interest.quantize(Decimal('0.00'))
                if rounded_interest <= Decimal('0.00'):
                    continue

                # Credit wallet
                CRMService.credit_wallet(
                    user=contract.user,
                    amount=rounded_interest,
                    contract=contract,
                    tx_type='INTEREST_PAYOUT',
                    description=f"Monthly Interest Payout for {month:02d}/{year} (Contract #{contract.id})"
                )

                # Mark all these logs as paid
                DailyInterestLog.objects.filter(
                    contract=contract,
                    date__year=year,
                    date__month=month
                ).update(
                    is_paid=True,
                    paid_at=timezone.now()
                )

                payouts_completed += 1
                total_payout_amount += rounded_interest

        return {
            "payouts_completed": payouts_completed,
            "total_payout_amount": float(total_payout_amount)
        }



    @staticmethod
    def calculate_cancellation_invoice(contract):
        """
        Helper to preview the cancellation penalty invoice details.
        Other Plans: 10% penalty of principal + Clawback of all previously PAID interest.
        1-Month Plan: Non-cancellable (will throw error).
        """
        if not contract.plan.is_cancellable:
            raise ValueError("1-Month Time Deposits are strictly non-cancellable.")

        penalty = (contract.principal * Decimal('0.10')).quantize(Decimal('0.00'))
        
        # Aggregate paid daily interest logs
        paid_logs = DailyInterestLog.objects.filter(contract=contract, is_paid=True)
        # However, interest is paid in rounded amounts. The sum of paid transactions is the exact amount to claw back.
        paid_transactions = WalletTransaction.objects.filter(
            contract=contract,
            type='INTEREST_PAYOUT'
        )
        clawback = sum(tx.amount for tx in paid_transactions)
        
        # Just in case transaction amounts are recorded as positive or negative, let's lock absolute values
        clawback = abs(clawback).quantize(Decimal('0.00'))
        
        refund = contract.principal - penalty - clawback
        if refund < 0:
            refund = Decimal('0.00')

        return {
            "principal": float(contract.principal),
            "penalty": float(penalty),
            "clawback": float(clawback),
            "refund": float(refund)
        }
