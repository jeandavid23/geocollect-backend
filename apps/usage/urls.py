from django.urls import path

from .views import OverviewView, TrackView

urlpatterns = [
    path('track/', TrackView.as_view(), name='usage_track'),
    path('overview/', OverviewView.as_view(), name='usage_overview'),
]
