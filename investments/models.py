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


class WalletTransaction(models.Model):
    TYPE_CHOICES = (
        ('DEPOSIT', 'Wallet Deposit'),
        ('WITHDRAWAL', 'Wallet Withdrawal'),
        ('INVESTMENT', 'Investment Block (MT5)'),
        ('INTEREST_PAYOUT', 'Interest Payout'),
        ('REFUND', 'Principal Refund'),
        ('PENALTY', 'Early Cancellation Penalty'),
        ('CLAWBACK', 'Interest Clawback'),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='transactions')
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Tx #{self.id} - {self.user.username} - {self.amount} USDT ({self.type})"


class CancellationRequest(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending Approval'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    )
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name='cancellation_requests')
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='submitted_cancellations')
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    penalty_amount = models.DecimalField(max_digits=15, decimal_places=2)
    clawback_interest_amount = models.DecimalField(max_digits=15, decimal_places=2)
    estimated_refund_amount = models.DecimalField(max_digits=15, decimal_places=2)
    refund_date = models.DateField() # Scheduled refund payout date (3rd to 5th of next month)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_cancellations')
    resolved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"Cancel Request #{self.id} - Contract #{self.contract.id} ({self.status})"


class IntegrationLog(models.Model):
    STATUS_CHOICES = (
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
    )
    action = models.CharField(max_length=50) # e.g. 'MT5_CREATE_ACCOUNT', 'MT5_LOCK_BALANCE'
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, null=True, blank=True)
    request_payload = models.TextField()
    response_payload = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Log #{self.id} - {self.action} ({self.status})"


class SimulationState(models.Model):
    virtual_date = models.DateField(default=date.today)

    def __str__(self):
        return f"Virtual Date: {self.virtual_date}"

