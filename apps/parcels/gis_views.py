"""
Édition SIG de la carte interactive (outils type QGIS) : enregistrement groupé des modifications.

POST /api/v1/parcels/gis/save/   (coopérative, super admin gestionnaire, propriétaire — jamais un agent)
{
  "cooperative": "<uuid>",                       # super admin / propriétaire (créations d'anciens polygones)
  "parcels": {"updates": [{"id", "geometry"?, "name"?, "village"?, "section"?, "culture"?}], "deletes": [id]},
  "legacy":  {"updates": [{"id", "geometry"?, "name"?, "properties"?}], "deletes": [id],
              "creates": [{"tmp_id", "name", "geometry", "properties"}]}
}
Tout ou rien (une transaction) : si un seul objet est introuvable ou hors de la coopérative, rien n'est enregistré.
Les surfaces, périmètres et la conformité EUDR des parcelles modifiées sont recalculés.
"""
import json
import logging
import math

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from shapely.geometry import mapping, shape

from apps.accounts.models import ActivityLog
from apps.accounts.notify import notify_users, coop_managers
from apps.accounts.permissions import IsCooperativeOrAdmin, resolve_cooperative, scope_to_cooperative
from apps.accounts.tenancy import has_module
from .deforestation import geodesic_area_ha
from .models import LegacyParcel, Parcel
from .validator_views import read_json_body

log = logging.getLogger(__name__)
MAX_OPS = 20000
PARCEL_FIELDS = ('name', 'village', 'section', 'culture')


class EditError(Exception):
    pass


def clean_geometry(geom, label):
    """Polygone / multipolygone GeoJSON en WGS84 : renvoie (géométrie, avertissement éventuel)."""
    try:
        g = shape(geom)
    except Exception:  # noqa: BLE001
        raise EditError(f'{label} : géométrie illisible.')
    if g.is_empty or g.geom_type not in ('Polygon', 'MultiPolygon'):
        raise EditError(f'{label} : un polygone est attendu.')
    minx, miny, maxx, maxy = g.bounds
    if not (-180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90):
        raise EditError(f'{label} : coordonnées hors du globe (WGS84 attendu).')
    warning = '' if g.is_valid else f'{label} : polygone invalide (auto-intersection), enregistré tel quel.'
    return mapping(g), warning


def _haversine(a, b):
    r = 6371008.8
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    dla, dlo = la2 - la1, math.radians(b[0] - a[0])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def measures(geom):
    polys = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
    perim = sum(_haversine(ring[i], ring[i + 1]) for p in polys for ring in p[:1] for i in range(len(ring) - 1))
    vertices = sum(max(0, len(p[0]) - 1) for p in polys if p)
    return round(geodesic_area_ha(geom), 4), round(perim, 1), vertices


class GisSaveView(APIView):
    permission_classes = [IsCooperativeOrAdmin]

    def post(self, request):
        try:
            body = read_json_body(request)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return Response({'detail': f'Corps illisible : {exc}'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(body, dict):
            return Response({'detail': 'Objet JSON attendu.'}, status=status.HTTP_400_BAD_REQUEST)
        p_ops = body.get('parcels') or {}
        l_ops = body.get('legacy') or {}
        n_ops = sum(len(x.get(k) or []) for x in (p_ops, l_ops) for k in ('updates', 'deletes', 'creates'))
        if n_ops == 0:
            return Response({'detail': 'Aucune modification à enregistrer.'}, status=status.HTTP_400_BAD_REQUEST)
        if n_ops > MAX_OPS:
            return Response({'detail': f'{n_ops} modifications : maximum {MAX_OPS} par enregistrement.'}, status=status.HTTP_400_BAD_REQUEST)
        if any(l_ops.get(k) for k in ('updates', 'deletes', 'creates')) and not has_module(request.user, 'legacy'):
            return Response({'detail': "Le module « Anciens polygones » n'est pas activé pour votre organisation."},
                            status=status.HTTP_403_FORBIDDEN)

        warnings, created_ids, coops = [], {}, set()
        try:
            with transaction.atomic():
                stats = {'parcels_updated': 0, 'parcels_deleted': 0, 'legacy_updated': 0, 'legacy_deleted': 0, 'legacy_created': 0}

                # ── Parcelles mappées : géométrie + attributs, jamais de création (elle passe par le mapping)
                p_qs = scope_to_cooperative(Parcel.objects.select_for_update(), request.user)
                updates = {str(u.get('id')): u for u in p_ops.get('updates') or [] if isinstance(u, dict)}
                found = {str(p.id): p for p in p_qs.filter(id__in=list(updates))} if updates else {}
                missing = set(updates) - set(found)
                if missing:
                    raise EditError(f'{len(missing)} parcelle(s) introuvable(s) ou hors de votre coopérative.')
                for pid, u in updates.items():
                    p = found[pid]
                    geom_changed = False
                    if u.get('geometry'):
                        geom, w = clean_geometry(u['geometry'], f'Parcelle {p.field_id}')
                        if w:
                            warnings.append(w)
                        p.geometry = geom
                        p.area_hectares, p.perimeter_meters, p.vertex_count = measures(geom)
                        geom_changed = True
                    for f in PARCEL_FIELDS:
                        if f in u and u[f] is not None:
                            setattr(p, f, str(u[f])[:255 if f == 'name' else 100])
                    p.save()
                    if geom_changed:
                        p.run_eudr_validation()
                    coops.add(p.cooperative_id)
                    stats['parcels_updated'] += 1
                p_del = [str(x) for x in p_ops.get('deletes') or []]
                if p_del:
                    qs = p_qs.filter(id__in=p_del)
                    if qs.count() != len(set(p_del)):
                        raise EditError('Parcelle(s) à supprimer introuvable(s) ou hors de votre coopérative.')
                    coops.update(qs.values_list('cooperative_id', flat=True))
                    stats['parcels_deleted'] = qs.delete()[0]

                # ── Anciens polygones : création, géométrie, nom, table attributaire
                l_qs = scope_to_cooperative(LegacyParcel.objects.select_for_update(), request.user)
                updates = {str(u.get('id')): u for u in l_ops.get('updates') or [] if isinstance(u, dict)}
                found = {str(p.id): p for p in l_qs.filter(id__in=list(updates))} if updates else {}
                missing = set(updates) - set(found)
                if missing:
                    raise EditError(f'{len(missing)} ancien(s) polygone(s) introuvable(s) ou hors de votre coopérative.')
                for lid, u in updates.items():
                    p = found[lid]
                    if u.get('geometry'):
                        geom, w = clean_geometry(u['geometry'], f'Polygone {p.name or lid[:8]}')
                        if w:
                            warnings.append(w)
                        p.geometry = geom
                        p.area_hectares = measures(geom)[0]
                    if 'name' in u and u['name'] is not None:
                        p.name = str(u['name'])[:255]
                    if isinstance(u.get('properties'), dict):
                        p.properties = u['properties']
                    p.save()
                    coops.add(p.cooperative_id)
                    stats['legacy_updated'] += 1
                l_del = [str(x) for x in l_ops.get('deletes') or []]
                if l_del:
                    qs = l_qs.filter(id__in=l_del)
                    if qs.count() != len(set(l_del)):
                        raise EditError('Polygone(s) à supprimer introuvable(s) ou hors de votre coopérative.')
                    coops.update(qs.values_list('cooperative_id', flat=True))
                    stats['legacy_deleted'] = qs.delete()[0]
                creates = [c for c in l_ops.get('creates') or [] if isinstance(c, dict)]
                if creates:
                    coop = resolve_cooperative(request, body)
                    if coop is None:
                        raise EditError('Coopérative requise pour créer des polygones.')
                    objs = []
                    for c in creates:
                        geom, w = clean_geometry(c.get('geometry'), f'Nouveau polygone {c.get("name") or ""}'.strip())
                        if w:
                            warnings.append(w)
                        obj = LegacyParcel(cooperative=coop, name=str(c.get('name') or '')[:255], geometry=geom,
                                           properties=c.get('properties') if isinstance(c.get('properties'), dict) else {},
                                           area_hectares=measures(geom)[0], source_file='Édition carte', uploaded_by=request.user)
                        objs.append((c.get('tmp_id'), obj))
                    LegacyParcel.objects.bulk_create([o for _, o in objs])
                    created_ids = {str(t): str(o.id) for t, o in objs if t}
                    coops.add(coop.id)
                    stats['legacy_created'] = len(objs)
        except EditError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001
            log.exception('Édition SIG')
            return Response({'detail': f'Enregistrement impossible : {exc}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        summary = ', '.join(f'{v} {k}' for k, v in (
            ('parcelle(s) modifiée(s)', stats['parcels_updated']), ('parcelle(s) supprimée(s)', stats['parcels_deleted']),
            ('polygone(s) modifié(s)', stats['legacy_updated']), ('polygone(s) supprimé(s)', stats['legacy_deleted']),
            ('polygone(s) créé(s)', stats['legacy_created'])) if v)
        ActivityLog.objects.create(user=request.user, action='gis_edit', resource='parcels', details=summary,
                                   ip_address=request.META.get('REMOTE_ADDR'))
        from apps.cooperatives.models import Cooperative
        who = request.user.full_name or request.user.username
        for coop in Cooperative.objects.filter(id__in=coops):
            notify_users(set(coop_managers(coop, with_owner=False)) - {request.user.id}, cooperative=coop,
                         title='Polygones modifiés sur la carte', message=f'{summary} — par {who}.')
        return Response({**stats, 'warnings': warnings[:50], 'created_ids': created_ids, 'summary': summary})
