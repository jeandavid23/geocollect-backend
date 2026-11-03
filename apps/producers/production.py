"""
Production estimée d'un producteur (registre) : colonne « Total estimate production (current year) » ou équivalent
(« Production estimée », « Estimated production »…). Elle sert de capacité de référence pour les volumes livrés :
au-delà, du cacao d'une autre origine a pu être mélangé.
"""
import re
import unicodedata

MAX_KG = 10 ** 8
CURRENT = ('current', 'en cours', 'actuel', 'cette annee', 'this year')
PREVIOUS = ('previous', 'last year', 'precedent', 'derniere', 'n 1', 'n-1')


def normalize(header):
    text = unicodedata.normalize('NFD', str(header)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def is_estimate_header(header):
    h = normalize(header)
    return 'estim' in h and 'produc' in h and not any(p in h for p in PREVIOUS)


def to_kg(value, header=''):
    """'1 250,5' → 1250.5 ; en tonnes si l'entête l'indique. None si illisible."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
    else:
        try:
            n = float(_number_text(str(value)))
        except ValueError:
            return None
    h = normalize(header)
    if re.search(r'\b(tonnes?|t)\b', h):
        n *= 1000
    return round(n, 1) if 0 <= n < MAX_KG else None


def _number_text(raw):
    """'1 250,5' → '1250.5' ; '2.500.000' → '2500000' ; '1.250,5' → '1250.5' (le dernier séparateur unique est décimal)."""
    text = re.sub(r'[^\d,.\-]', '', raw)
    if ',' in text and '.' in text:
        decimal = ',' if text.rfind(',') > text.rfind('.') else '.'
        text = text.replace('.' if decimal == ',' else ',', '').replace(',', '.')
    elif text.count(',') > 1 or text.count('.') > 1:              # séparateurs de milliers seulement
        text = text.replace(',', '').replace('.', '')
    else:
        text = text.replace(',', '.')
    return text


def from_extra(extra):
    """Production estimée (kg) trouvée dans les colonnes d'origine du registre, ou None."""
    candidates = [(k, v) for k, v in (extra or {}).items() if is_estimate_header(k)]
    candidates.sort(key=lambda kv: not any(c in normalize(kv[0]) for c in CURRENT))     # « année en cours » d'abord
    for header, value in candidates:
        kg = to_kg(value, header)
        if kg is not None:
            return kg
    return None
