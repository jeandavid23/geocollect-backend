from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('me/', views.MeView.as_view(), name='me'),
    path('change-password/', views.ChangePasswordView.as_view(), name='change_password'),
    path('users/', views.UserListCreateView.as_view(), name='user_list'),
    path('users/<uuid:pk>/', views.UserDetailView.as_view(), name='user_detail'),
    path('users/<uuid:pk>/toggle/', views.ToggleUserActiveView.as_view(), name='user_toggle'),
    path('users/<uuid:pk>/reset-password/', views.ResetPasswordView.as_view(), name='user_reset_password'),
    path('logs/', views.ActivityLogListView.as_view(), name='activity_logs'),
]
