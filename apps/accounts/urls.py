from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views
from .mfa_views import MfaVerifyView, MfaSetupView, MfaResetView
from .google_auth import GoogleConfigView, GoogleLoginView
from .messaging import MessageTargetsView, SendMessageView

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('google/', GoogleLoginView.as_view(), name='google_login'),
    path('google/config/', GoogleConfigView.as_view(), name='google_config'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('me/', views.MeView.as_view(), name='me'),
    path('change-password/', views.ChangePasswordView.as_view(), name='change_password'),
    path('users/', views.UserListCreateView.as_view(), name='user_list'),
    path('mfa/verify/', MfaVerifyView.as_view(), name='mfa_verify'),
    path('mfa/setup/', MfaSetupView.as_view(), name='mfa_setup'),
    path('users/<uuid:pk>/reset-mfa/', MfaResetView.as_view(), name='user_reset_mfa'),
    path('users/delete-preview/', views.UserDeletePreviewView.as_view(), name='user_delete_preview'),
    path('users/bulk-delete/', views.UserBulkDeleteView.as_view(), name='user_bulk_delete'),
    path('users/<uuid:pk>/', views.UserDetailView.as_view(), name='user_detail'),
    path('users/<uuid:pk>/toggle/', views.ToggleUserActiveView.as_view(), name='user_toggle'),
    path('users/<uuid:pk>/reset-password/', views.ResetPasswordView.as_view(), name='user_reset_password'),
    path('logs/', views.ActivityLogListView.as_view(), name='activity_logs'),
    path('notifications/', views.NotificationListView.as_view(), name='notifications'),
    path('notifications/targets/', MessageTargetsView.as_view(), name='message_targets'),
    path('notifications/send/', SendMessageView.as_view(), name='message_send'),
    path('notifications/clear/', views.NotificationDeleteView.as_view(), name='notifications_clear'),
    path('notifications/read/', views.NotificationMarkReadView.as_view(), name='notifications_read'),
]
