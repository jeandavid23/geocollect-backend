from django.urls import path

from .views import LotDetailView, LotListView

urlpatterns = [
    path('', LotListView.as_view(), name='lot_list'),
    path('<uuid:pk>/', LotDetailView.as_view(), name='lot_detail'),
]
