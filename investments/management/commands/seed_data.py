from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from decimal import Decimal
from datetime import date
from investments.models import InvestmentPlan, SimulationState, Profile

class Command(BaseCommand):
    help = "Seed initial plans, users, and simulation date for FaradBank Investment Engine"

    def handle(self, *args, **options):
        self.stdout.write("Seeding data...")

        # 1. Seed Simulation State
        state, created = SimulationState.objects.get_or_create(id=1)
        # Seed it with current date from metadata: 2026-05-19
        state.virtual_date = date(2026, 5, 19)
        state.save()
        self.stdout.write(f"Simulation date initialized to {state.virtual_date}")

        # 2. Seed Investment Plans
        plans_data = [
            {
                "id": "1m",
                "name": "1-Month Liquidity Plan",
                "duration_months": 1,
                "interest_rate_apy": Decimal("10.00"),
                "min_deposit": Decimal("100.00"),
                "is_cancellable": False,
                "cancellation_penalty_pct": Decimal("0.00")
            },
            {
                "id": "3m",
                "name": "3-Month Quarter Plan",
                "duration_months": 3,
                "interest_rate_apy": Decimal("12.00"),
                "min_deposit": Decimal("100.00"),
                "is_cancellable": True,
                "cancellation_penalty_pct": Decimal("10.00")
            },
            {
                "id": "6m",
                "name": "6-Month Growth Plan",
                "duration_months": 6,
                "interest_rate_apy": Decimal("14.00"),
                "min_deposit": Decimal("100.00"),
                "is_cancellable": True,
                "cancellation_penalty_pct": Decimal("10.00")
            },
            {
                "id": "1y",
                "name": "1-Year Premium Plan",
                "duration_months": 12,
                "interest_rate_apy": Decimal("16.00"),
                "min_deposit": Decimal("100.00"),
                "is_cancellable": True,
                "cancellation_penalty_pct": Decimal("10.00")
            },
            {
                "id": "2y",
                "name": "2-Year Wealth Builder",
                "duration_months": 24,
                "interest_rate_apy": Decimal("18.00"),
                "min_deposit": Decimal("100.00"),
                "is_cancellable": True,
                "cancellation_penalty_pct": Decimal("10.00")
            },
            {
                "id": "5y",
                "name": "5-Year Horizon Pension",
                "duration_months": 60,
                "interest_rate_apy": Decimal("22.00"),
                "min_deposit": Decimal("100.00"),
                "is_cancellable": True,
                "cancellation_penalty_pct": Decimal("10.00")
            }
        ]

        for p_data in plans_data:
            plan, created = InvestmentPlan.objects.get_or_create(id=p_data["id"], defaults=p_data)
            if not created:
                # Update attributes
                for key, val in p_data.items():
                    setattr(plan, key, val)
                plan.save()
            self.stdout.write(f"Plan {plan.id} - {plan.name} seeded successfully.")

        # 3. Seed Users
        # Investor user
        investor_user, created = User.objects.get_or_create(username="investor")
        if created:
            investor_user.set_password("password123")
            investor_user.email = "investor@faradbank.com"
            investor_user.save()
        investor_user.profile.role = "INVESTOR"
        if investor_user.profile.balance == Decimal("0.00"):
            investor_user.profile.balance = Decimal("10000.00") # Start with 10k USDT
        investor_user.profile.save()
        self.stdout.write(f"User 'investor' set up as INVESTOR with {investor_user.profile.balance} USDT.")

        # Manager user
        manager_user, created = User.objects.get_or_create(username="manager")
        if created:
            manager_user.set_password("password123")
            manager_user.email = "manager@faradbank.com"
            manager_user.save()
        manager_user.profile.role = "MANAGER"
        manager_user.profile.save()
        self.stdout.write("User 'manager' set up as MANAGER.")

        self.stdout.write(self.style.SUCCESS("Database seeding completed successfully!"))
