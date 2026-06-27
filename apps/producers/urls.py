from django.urls import path
from . import views

urlpatterns = [
    path('agents/', views.AgentListCreateView.as_view(), name='agent_list'),
    path('agents/<uuid:pk>/', views.AgentDetailView.as_view(), name='agent_detail'),
    path('producers/', views.ProducerListCreateView.as_view(), name='producer_list'),
    path('producers/<uuid:pk>/', views.ProducerDetailView.as_view(), name='producer_detail'),
]
