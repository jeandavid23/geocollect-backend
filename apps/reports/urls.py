from django.urls import path

from .views import ReportDetailView, ReportListCreateView

urlpatterns = [
    path('', ReportListCreateView.as_view(), name='report_list'),
    path('<uuid:pk>/', ReportDetailView.as_view(), name='report_detail'),
]
