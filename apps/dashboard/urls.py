from django.urls import path
from . import views

urlpatterns = [
    path('admin/', views.AdminDashboardView.as_view(), name='admin_dashboard'),
    path('coop/', views.CoopDashboardView.as_view(), name='coop_dashboard'),
    path('agent/', views.AgentDashboardView.as_view(), name='agent_dashboard'),
]
