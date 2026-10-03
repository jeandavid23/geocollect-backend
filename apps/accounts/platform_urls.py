from django.urls import path
from . import platform_views as v

urlpatterns = [
    path('overview/', v.PlatformOverviewView.as_view(), name='platform_overview'),
    path('admins/', v.PlatformAdminListView.as_view(), name='platform_admins'),
    path('admins/<uuid:pk>/', v.PlatformAdminDetailView.as_view(), name='platform_admin_detail'),
    path('cooperatives/<uuid:pk>/assign/', v.PlatformAssignCooperativeView.as_view(), name='platform_assign_coop'),
]
