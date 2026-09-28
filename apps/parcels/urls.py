from django.urls import path
from . import views
from .deforestation_views import DeforestationAnalyzeView
from .legacy_views import LegacyParcelListView, LegacyParcelImportView, LegacyParcelSourcesView

urlpatterns = [
    path('deforestation/analyze/', DeforestationAnalyzeView.as_view(), name='deforestation_analyze'),
    path('legacy/', LegacyParcelListView.as_view(), name='legacy_parcel_list'),
    path('legacy/import/', LegacyParcelImportView.as_view(), name='legacy_parcel_import'),
    path('legacy/sources/', LegacyParcelSourcesView.as_view(), name='legacy_parcel_sources'),
    path('', views.ParcelListCreateView.as_view(), name='parcel_list'),
    path('<uuid:pk>/', views.ParcelDetailView.as_view(), name='parcel_detail'),
    path('<uuid:pk>/validate/', views.ParcelValidateView.as_view(), name='parcel_validate'),
    path('geojson/', views.ParcelGeoJSONView.as_view(), name='parcel_geojson'),
    path('export/csv/', views.ParcelExportCSVView.as_view(), name='parcel_export_csv'),
    path('sync/', views.SyncParcelsView.as_view(), name='parcel_sync'),
]
