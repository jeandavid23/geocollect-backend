from django.urls import path

from . import views

urlpatterns = [
    path('metrics/', views.metrics, name='monitoring_metrics'),
    path('health/', views.health, name='monitoring_health'),
    path('client-error/', views.client_error, name='monitoring_client_error'),
]
