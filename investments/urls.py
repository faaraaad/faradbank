from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AuthLoginView, WalletView, TransactionHistoryView, 
    InvestmentPlanViewSet, ContractViewSet, SimulationStateView, SimulationTimeTravelView
)

router = DefaultRouter()
router.register(r'plans', InvestmentPlanViewSet, basename='plan')
router.register(r'contracts', ContractViewSet, basename='contract')

urlpatterns = [
    path('auth/login/', AuthLoginView.as_view(), name='auth_login'),
    path('wallet/', WalletView.as_view(), name='wallet'),
    path('wallet/transactions/', TransactionHistoryView.as_view(), name='wallet_transactions'),
    
    # Simulation Control
    path('simulation/state/', SimulationStateView.as_view(), name='simulation_state'),
    path('simulation/time-travel/', SimulationTimeTravelView.as_view(), name='simulation_time_travel'),
    
    path('', include(router.urls)),
]
