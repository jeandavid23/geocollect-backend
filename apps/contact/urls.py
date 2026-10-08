from django.urls import path

from .views import DemoDetailView, DemoListView, DemoRequestView

urlpatterns = [
    path('demo/', DemoRequestView.as_view(), name='demo_request'),
    path('demo/list/', DemoListView.as_view(), name='demo_list'),
    path('demo/<uuid:pk>/', DemoDetailView.as_view(), name='demo_detail'),
]
