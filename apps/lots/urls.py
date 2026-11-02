from django.urls import path

from .views import LotDetailView, LotListView
from .views_processing import ProcessingDetailView, ProcessingListView, ProcessingTraceView
from .views_trace import (DeliveryCancelView, DeliveryDetailView, DeliveryListView, TransportDetailView,
                          TransportListView, VolumesView)

urlpatterns = [
    path('', LotListView.as_view(), name='lot_list'),
    path('<uuid:pk>/', LotDetailView.as_view(), name='lot_detail'),
    path('deliveries/', DeliveryListView.as_view(), name='delivery_list'),
    path('deliveries/<uuid:pk>/', DeliveryDetailView.as_view(), name='delivery_detail'),
    path('deliveries/<uuid:pk>/cancel/', DeliveryCancelView.as_view(), name='delivery_cancel'),
    path('volumes/', VolumesView.as_view(), name='lot_volumes'),
    path('transport/', TransportListView.as_view(), name='transport_list'),
    path('transport/<uuid:pk>/', TransportDetailView.as_view(), name='transport_detail'),
    path('processing/', ProcessingListView.as_view(), name='processing_list'),
    path('processing/<uuid:pk>/', ProcessingDetailView.as_view(), name='processing_detail'),
    path('processing/<uuid:pk>/trace/', ProcessingTraceView.as_view(), name='processing_trace'),
]
