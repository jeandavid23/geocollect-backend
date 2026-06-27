from django.urls import path
from . import views

urlpatterns = [
    path('', views.ParcelListCreateView.as_view(), name='parcel_list'),
    path('<uuid:pk>/', views.ParcelDetailView.as_view(), name='parcel_detail'),
    path('<uuid:pk>/validate/', views.ParcelValidateView.as_view(), name='parcel_validate'),
    path('geojson/', views.ParcelGeoJSONView.as_view(), name='parcel_geojson'),
    path('export/csv/', views.ParcelExportCSVView.as_view(), name='parcel_export_csv'),
    path('sync/', views.SyncParcelsView.as_view(), name='parcel_sync'),
]
