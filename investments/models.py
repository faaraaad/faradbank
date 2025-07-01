from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from decimal import Decimal
from datetime import date

class Profile(models.Model):
    ROLE_CHOICES = (
        ('INVESTOR', 'Investor'),
        ('MANAGER', 'Manager'),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    balance = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal('0.00')) # USDT Wallet balance
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='INVESTOR')

    def __str__(self):
        return f"{self.user.username} ({self.role}) - Balance: {self.balance} USDT"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'profile'):
        instance.profile.save()


class InvestmentPlan(models.Model):
    id = models.CharField(max_length=10, primary_key=True) # e.g. '1m', '3m', '6m', '1y', '2y', '5y'
    name = models.CharField(max_length=100)
    duration_months = models.IntegerField()
    interest_rate_apy = models.DecimalField(max_digits=5, decimal_places=2) # e.g. 12.00 for 12%
    min_deposit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('100.00'))
    is_cancellable = models.BooleanField(default=True)
    cancellation_penalty_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('10.00'))

    def __str__(self):
        return f"{self.name} ({self.interest_rate_apy}% APY)"


class Contract(models.Model):
    STATUS_CHOICES = (
        ('ACTIVE', 'Active'),
        ('PENDING_CANCELLATION', 'Pending Cancellation'),
        ('CANCELLED', 'Cancelled'),
        ('MATURED', 'Matured'),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='contracts')
    plan = models.ForeignKey(InvestmentPlan, on_delete=models.PROTECT, related_name='contracts')
    principal = models.DecimalField(max_digits=15, decimal_places=2)
    interest_rate_apy = models.DecimalField(max_digits=5, decimal_places=2) # APY locked at contract start
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='ACTIVE')
    start_date = models.DateField()
    maturity_date = models.DateField()
    auto_renew = models.BooleanField(default=False)
    mt5_account_id = models.CharField(max_length=50, blank=True, null=True)
    pending_upgrade_apy = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True) # delayed rate change for next month
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Contract #{self.id} - {self.user.username} - {self.principal} USDT ({self.plan.id})"


class DailyInterestLog(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name='daily_interest_logs')
    date = models.DateField()
    amount = models.DecimalField(max_digits=15, decimal_places=6)
    is_paid = models.BooleanField(default=False) # Whether it has been paid in a monthly payout
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('contract', 'date')

    def __str__(self):
        return f"Daily Log Contract #{self.contract.id} on {self.date}: {self.amount} USDT"


