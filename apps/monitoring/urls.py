from django.urls import path

from . import views

urlpatterns = [
    path('metrics/', views.metrics, name='monitoring_metrics'),
    # sans barre finale : pas de redirection 301 (certains collecteurs perdent l'authentification en la suivant)
    path('metrics', views.metrics),
    path('health/', views.health, name='monitoring_health'),
    path('client-error/', views.client_error, name='monitoring_client_error'),
]
