from django.urls import path
from . import views

urlpatterns = [
    path('', views.CooperativeListCreateView.as_view(), name='cooperative_list'),
    path('<uuid:pk>/', views.CooperativeDetailView.as_view(), name='cooperative_detail'),
]
