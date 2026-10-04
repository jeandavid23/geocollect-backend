from django.urls import path
from . import views
from .deforestation_views import DeforestationAnalyzeView, LandZoneListView
from .legacy_views import LegacyParcelListView, LegacyParcelImportView, LegacyParcelSourcesView
from .validator_views import PolygonValidatorView
from .gis_views import GisSaveView
from .selfintersection_views import SelfIntersectionView, GmrUsageView, DeforestationDoneView

urlpatterns = [
    path('deforestation/analyze/', DeforestationAnalyzeView.as_view(), name='deforestation_analyze'),
    path('landuse/', LandZoneListView.as_view(), name='landuse_list'),
    path('validator/run/', PolygonValidatorView.as_view(), name='polygon_validator'),
    path('selfintersection/run/', SelfIntersectionView.as_view(), name='self_intersection'),
    path('deforestation/done/', DeforestationDoneView.as_view(), name='deforestation_done'),
    path('gmr/log/', GmrUsageView.as_view(), name='gmr_usage'),
    path('gis/save/', GisSaveView.as_view(), name='gis_save'),
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
