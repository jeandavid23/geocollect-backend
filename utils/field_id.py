"""
Génération automatique des FIELD IDs.
Format : SECTION000245 (base) → SECTION000245-M001 (parcelle)
"""


def generate_field_id_base(section: str, index: int) -> str:
    code = section.upper().replace(' ', '')[:10]
    return f'{code}{str(index).zfill(6)}'


def generate_parcel_field_id(base: str, parcel_index: int) -> str:
    return f'{base}-M{str(parcel_index).zfill(3)}'


def get_next_producer_index(cooperative_id, section: str) -> int:
    from apps.producers.models import Producer
    count = Producer.objects.filter(
        cooperative_id=cooperative_id,
        section=section,
    ).count()
    return count + 1


def get_next_parcel_index(producer_id) -> int:
    from apps.parcels.models import Parcel
    return Parcel.objects.filter(producer_id=producer_id).count() + 1


def find_existing_producer(cooperative_id, first_name: str, last_name: str, village: str):
    from apps.producers.models import Producer
    return Producer.objects.filter(
        cooperative_id=cooperative_id,
        first_name__iexact=first_name,
        last_name__iexact=last_name,
        village__iexact=village,
    ).first()
