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


