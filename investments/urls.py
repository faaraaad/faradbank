from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AuthLoginView, WalletView, TransactionHistoryView, 
    InvestmentPlanViewSet, ContractViewSet
)

router = DefaultRouter()
router.register(r'plans', InvestmentPlanViewSet, basename='plan')
router.register(r'contracts', ContractViewSet, basename='contract')

urlpatterns = [
    path('auth/login/', AuthLoginView.as_view(), name='auth_login'),
    path('wallet/', WalletView.as_view(), name='wallet'),
    path('wallet/transactions/', TransactionHistoryView.as_view(), name='wallet_transactions'),
    path('', include(router.urls)),
]
