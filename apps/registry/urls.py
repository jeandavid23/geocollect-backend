from django.urls import path
from . import views

urlpatterns = [
    path('sheets/', views.RegistrySheetListCreateView.as_view(), name='registry_sheet_list'),
    path('sheets/<uuid:pk>/', views.RegistrySheetDetailView.as_view(), name='registry_sheet_detail'),
    path('import/', views.RegistryImportView.as_view(), name='registry_import'),
]
