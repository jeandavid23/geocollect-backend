from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    return Response({
        'status': 'ok',
        'service': 'GeoCollect EUDR API',
        'version': '1.0.0',
    })


urlpatterns = [
    # Health check
    path('', health_check, name='health'),

    # Django Admin
    path('django-admin/', admin.site.urls),

    # API v1
    path('api/v1/auth/', include('apps.accounts.urls')),
    path('api/v1/cooperatives/', include('apps.cooperatives.urls')),
    path('api/v1/', include('apps.producers.urls')),
    path('api/v1/parcels/', include('apps.parcels.urls')),
    path('api/v1/dashboard/', include('apps.dashboard.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
