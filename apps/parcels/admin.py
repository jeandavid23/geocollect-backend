from django.contrib import admin
from .models import LegacyParcel


@admin.register(LegacyParcel)
class LegacyParcelAdmin(admin.ModelAdmin):
    list_display = ('name', 'cooperative', 'source_file', 'area_hectares', 'created_at')
    list_filter = ('cooperative', 'source_file')
    exclude = ('geometry',)
