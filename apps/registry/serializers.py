from rest_framework import serializers
from .models import RegistrySheet

MAX_ROWS = 20000
MAX_COLS = 500


class RegistrySheetSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.CharField(source='updated_by.full_name', read_only=True, default='')

    class Meta:
        model = RegistrySheet
        fields = ['id', 'cooperative', 'name', 'position', 'data', 'col_widths',
                  'source_file', 'updated_by_name', 'created_at', 'updated_at']
        read_only_fields = ['id', 'cooperative', 'created_at', 'updated_at']

    def validate_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Nom de feuille requis.')
        return value[:100]

    def validate_data(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('Tableau de lignes attendu.')
        if len(value) > MAX_ROWS:
            raise serializers.ValidationError(f'Maximum {MAX_ROWS} lignes par feuille.')
        for row in value:
            if not isinstance(row, list):
                raise serializers.ValidationError('Chaque ligne doit être un tableau de cellules.')
            if len(row) > MAX_COLS:
                raise serializers.ValidationError(f'Maximum {MAX_COLS} colonnes par feuille.')
            for cell in row:
                if cell is not None and not isinstance(cell, (str, int, float, bool)):
                    raise serializers.ValidationError('Cellule invalide (texte, nombre ou booléen attendu).')
        return value
