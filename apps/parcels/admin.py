from django.contrib import admin
from .models import LandZone, LegacyParcel


@admin.register(LegacyParcel)
class LegacyParcelAdmin(admin.ModelAdmin):
    list_display = ('name', 'cooperative', 'source_file', 'area_hectares', 'created_at')
    list_filter = ('cooperative', 'source_file')
    exclude = ('geometry',)


@admin.register(LandZone)
class LandZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'region', 'source_file')
    list_filter = ('category',)
    search_fields = ('name', 'region')
    exclude = ('geometry',)
