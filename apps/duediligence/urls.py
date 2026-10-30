from django.urls import path

from . import views

urlpatterns = [
    path('catalog/', views.CatalogView.as_view(), name='dd_catalog'),
    path('settings/', views.SettingsView.as_view(), name='dd_settings'),
    path('analyses/', views.AnalysisListView.as_view(), name='dd_analyses'),
    path('analyses/<uuid:pk>/', views.AnalysisDetailView.as_view(), name='dd_analysis'),
    path('analyses/<uuid:pk>/status/', views.AnalysisStatusView.as_view(), name='dd_analysis_status'),
    path('analyses/<uuid:pk>/decisions/', views.DecisionView.as_view(), name='dd_decisions'),
    path('analyses/<uuid:pk>/whisp-export/', views.WhispExportView.as_view(), name='dd_whisp_export'),
    path('analyses/<uuid:pk>/whisp-import/', views.WhispImportView.as_view(), name='dd_whisp_import'),
]
