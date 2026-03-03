from django.test import TestCase
from django.contrib.auth.models import User
from decimal import Decimal
from datetime import date, timedelta

from investments.models import (
    InvestmentPlan, Contract, DailyInterestLog, WalletTransaction, 
    CancellationRequest, SimulationState
)
from investments.services import CRMService, MT5Service, InterestEngineService


class FaradBankBusinessRulesTestCase(TestCase):
    def setUp(self):
        # Setup plans
        self.plan_1m = InvestmentPlan.objects.create(
            id="1m",
            name="1-Month Liquidity Plan",
            duration_months=1,
            interest_rate_apy=Decimal("10.00"),
            min_deposit=Decimal("100.00"),
            is_cancellable=False
        )
        self.plan_3m = InvestmentPlan.objects.create(
            id="3m",
            name="3-Month Quarter Plan",
            duration_months=3,
            interest_rate_apy=Decimal("12.00"),
            min_deposit=Decimal("100.00"),
            is_cancellable=True,
            cancellation_penalty_pct=Decimal("10.00")
        )
        self.plan_1y = InvestmentPlan.objects.create(
            id="1y",
            name="1-Year Premium Plan",
            duration_months=12,
            interest_rate_apy=Decimal("16.00"),
            min_deposit=Decimal("100.00"),
            is_cancellable=True,
            cancellation_penalty_pct=Decimal("10.00")
        )

        # Setup users
        self.investor = User.objects.create_user(username="test_investor", password="password")
        self.investor.profile.balance = Decimal("10000.00")
        self.investor.profile.save()

        # Setup simulation state
        self.state = SimulationState.objects.create(id=1, virtual_date=date(2026, 5, 19))

    def test_daily_interest_math(self):
        # Create a contract for 5000 USDT on 3-Month Plan (12% APY)
        start_date = date(2026, 5, 19)
        contract = Contract.objects.create(
            user=self.investor,
            plan=self.plan_3m,
            principal=Decimal("5000.00"),
            interest_rate_apy=Decimal("12.00"),
            status='ACTIVE',
            start_date=start_date,
            maturity_date=start_date + timedelta(days=90)
        )

        # Run daily calculation for day 1
        logs_created = InterestEngineService.calculate_daily_interest(start_date)
        self.assertEqual(logs_created, 1)

        # Daily math check: (5000 * 0.12) / 365 = 1.643836 USDT
        log = DailyInterestLog.objects.get(contract=contract, date=start_date)
        expected_interest = Decimal("5000.00") * Decimal("0.12") / Decimal("365.00")
        expected_rounded = expected_interest.quantize(Decimal("0.000001"))
        
        self.assertEqual(log.amount, expected_rounded)
        self.assertEqual(float(log.amount), 1.643836)

