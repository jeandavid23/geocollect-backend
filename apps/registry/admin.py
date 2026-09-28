from django.contrib import admin
from .models import RegistrySheet


@admin.register(RegistrySheet)
class RegistrySheetAdmin(admin.ModelAdmin):
    list_display = ('name', 'cooperative', 'position', 'source_file', 'updated_at')
    list_filter = ('cooperative',)
    exclude = ('data',)
