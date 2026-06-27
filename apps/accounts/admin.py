from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, ActivityLog


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'full_name', 'role', 'cooperative', 'is_active', 'created_at']
    list_filter = ['role', 'is_active', 'cooperative']
    search_fields = ['username', 'full_name', 'email']
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Informations', {'fields': ('full_name', 'email', 'phone', 'role', 'cooperative', 'avatar')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'full_name', 'role', 'cooperative', 'password1', 'password2'),
        }),
    )
    ordering = ['-created_at']


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'action', 'resource', 'ip_address', 'timestamp']
    list_filter = ['action', 'resource']
    search_fields = ['user__username', 'action']
    readonly_fields = ['timestamp']
