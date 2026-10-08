from django.urls import path
from . import views

urlpatterns = [
    path('agents/', views.AgentListCreateView.as_view(), name='agent_list'),
    path('agents/<uuid:pk>/', views.AgentDetailView.as_view(), name='agent_detail'),
    path('producers/', views.ProducerListCreateView.as_view(), name='producer_list'),
    path('producers/purge/', views.BasePurgeView.as_view(), name='base_purge'),
    path('producers/recode/', views.ProducerRecodeView.as_view(), name='producer_recode'),
    path('producers/match/', views.ProducerMatchView.as_view(), name='producer_match'),
    path('producers/bulk-delete/', views.ProducerBulkDeleteView.as_view(), name='producer_bulk_delete'),
    path('producers/bulk/', views.ProducerBulkImportView.as_view(), name='producer_bulk'),
    path('producers/<uuid:pk>/', views.ProducerDetailView.as_view(), name='producer_detail'),
]
