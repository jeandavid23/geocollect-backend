from django.db.models import Count, Sum, Avg
from django.utils import timezone
from datetime import timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions

from apps.cooperatives.models import Cooperative
from apps.producers.models import Producer, Agent
from apps.parcels.models import Parcel
from apps.accounts.permissions import IsAgentOrAbove, IsCooperativeOrAdmin, IsSuperAdmin, scope_to_cooperative, managed_cooperatives


class AdminDashboardView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        today = timezone.now().date()
        week_ago = today - timedelta(days=7)

        # propriétaire : toute la plateforme ; super admin : uniquement ses coopératives
        user = request.user
        coops = managed_cooperatives(user)
        parcels = scope_to_cooperative(Parcel.objects.all(), user)
        coop_count = coops.filter(is_active=True).count()
        producer_count = scope_to_cooperative(Producer.objects.filter(is_active=True), user).count()
        parcel_count = parcels.count()
        agent_count = scope_to_cooperative(Agent.objects.filter(is_active=True), user).count()
        total_ha = parcels.aggregate(t=Sum('area_hectares'))['t'] or 0

        eudr = parcels.values('eudr_status').annotate(count=Count('id'))
        eudr_map = {e['eudr_status']: e['count'] for e in eudr}

        # Daily progress last 14 days
        daily = []
        for i in range(13, -1, -1):
            day = today - timedelta(days=i)
            count = parcels.filter(created_at__date=day).count()
            ha = parcels.filter(created_at__date=day).aggregate(s=Sum('area_hectares'))['s'] or 0
            daily.append({'date': day.strftime('%d %b'), 'parcels': count, 'hectares': round(ha, 2)})

        cooperatives = coops.annotate(
            p_count=Count('parcels'),
            ha=Sum('parcels__area_hectares'),
        ).values('id', 'name', 'region', 'p_count', 'ha')[:10]

        return Response({
            'total_cooperatives': coop_count,
            'total_producers': producer_count,
            'total_parcels': parcel_count,
            'total_agents': agent_count,
            'total_hectares': round(total_ha, 2),
            'eudr_compliant': eudr_map.get('compliant', 0),
            'eudr_non_compliant': eudr_map.get('non_compliant', 0),
            'eudr_pending': eudr_map.get('pending', 0),
            'avg_eudr_score': parcels.filter(eudr_score__isnull=False).aggregate(a=Avg('eudr_score'))['a'],
            'daily_progress': daily,
            'cooperatives': list(cooperatives),
        })


class CoopDashboardView(APIView):
    permission_classes = [IsCooperativeOrAdmin]

    def get(self, request):
        coop = request.user.cooperative
        if not coop:
            return Response({'detail': 'Aucune coopérative associée.'}, status=400)

        today = timezone.now().date()
        parcels = Parcel.objects.filter(cooperative=coop)
        producers = Producer.objects.filter(cooperative=coop)
        agents = Agent.objects.filter(cooperative=coop)

        eudr = parcels.values('eudr_status').annotate(count=Count('id'))
        eudr_map = {e['eudr_status']: e['count'] for e in eudr}

        sections = list(parcels.values('section').annotate(
            count=Count('id'),
            ha=Sum('area_hectares'),
        ).order_by('-count')[:10])

        villages = list(parcels.values('village').distinct().count())

        daily = []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            count = parcels.filter(created_at__date=day).count()
            ha = parcels.filter(created_at__date=day).aggregate(s=Sum('area_hectares'))['s'] or 0
            daily.append({'date': day.strftime('%d %b'), 'parcels': count, 'hectares': round(ha, 2)})

        top_agents = agents.annotate(
            parcel_count=Count('parcels'),
            total_ha=Sum('parcels__area_hectares'),
        ).order_by('-parcel_count')[:5]

        return Response({
            'cooperative': {'id': str(coop.id), 'name': coop.name},
            'total_producers': producers.count(),
            'total_parcels': parcels.count(),
            'total_hectares': round(parcels.aggregate(t=Sum('area_hectares'))['t'] or 0, 2),
            'total_agents': agents.count(),
            'total_villages': parcels.values('village').distinct().count(),
            'total_sections': parcels.values('section').distinct().count(),
            'eudr_compliant': eudr_map.get('compliant', 0),
            'eudr_non_compliant': eudr_map.get('non_compliant', 0),
            'eudr_pending': eudr_map.get('pending', 0),
            'daily_progress': daily,
            'top_sections': sections,
            'top_agents': [
                {
                    'agent_id': str(a.id),
                    'agent_name': a.user.full_name,
                    'parcels': a.parcel_count,
                    'hectares': round(a.total_ha or 0, 2),
                }
                for a in top_agents
            ],
        })


class AgentDashboardView(APIView):
    permission_classes = [IsAgentOrAbove]

    def get(self, request):
        agent = getattr(request.user, 'agent_profile', None)
        if not agent:
            return Response({'detail': 'Profil agent introuvable.'}, status=400)

        today = timezone.now().date()
        parcels = Parcel.objects.filter(agent=agent)
        producers = Producer.objects.filter(assigned_agent=agent)

        eudr = parcels.values('eudr_status').annotate(count=Count('id'))
        eudr_map = {e['eudr_status']: e['count'] for e in eudr}

        weekly = []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            count = parcels.filter(created_at__date=day).count()
            ha = parcels.filter(created_at__date=day).aggregate(s=Sum('area_hectares'))['s'] or 0
            weekly.append({'date': day.strftime('%a'), 'parcels': count, 'hectares': round(ha, 2)})

        recent = parcels.select_related('producer').order_by('-created_at')[:10]

        return Response({
            'agent': {
                'id': str(agent.id),
                'code': agent.code,
                'name': request.user.full_name,
                'zone': agent.zone,
            },
            'total_parcels': parcels.count(),
            'today_parcels': parcels.filter(created_at__date=today).count(),
            'total_hectares': round(parcels.aggregate(t=Sum('area_hectares'))['t'] or 0, 2),
            'total_producers': producers.count(),
            'eudr_compliant': eudr_map.get('compliant', 0),
            'eudr_non_compliant': eudr_map.get('non_compliant', 0),
            'unsynced_parcels': parcels.filter(is_synced=False).count(),
            'weekly_progress': weekly,
            'recent_parcels': [
                {
                    'field_id': p.field_id,
                    'village': p.village,
                    'area_hectares': p.area_hectares,
                    'eudr_score': p.eudr_score,
                    'eudr_status': p.eudr_status,
                    'is_synced': p.is_synced,
                    'created_at': p.created_at,
                }
                for p in recent
            ],
        })


class CoopActionsView(APIView):
    """
    GET /api/v1/dashboard/coop/actions/[?cooperative=&refresh=1]
    « Prochaines actions » de la coopérative : ce qui reste à faire pour être prête à exporter, du plus urgent
    au moins urgent, avec la page où le faire ; et l'avancement en 4 étapes (registre → polygones → contrôles → export).
    Les contrôles spatiaux (chevauchements, zones foncières) sont gardés 10 minutes en cache.
    """
    permission_classes = [IsCooperativeOrAdmin]

    def get(self, request):
        from django.core.cache import cache
        from apps.accounts.permissions import resolve_cooperative
        from apps.lots.models import Lot
        from apps.lots.views import checks as lot_checks
        from apps.parcels import spatial
        from apps.parcels.models import LegacyParcel
        from apps.producers.matching import stats as match_stats
        from apps.registry.models import RegistrySheet
        from apps.reports.models import AnalysisReport

        coop = resolve_cooperative(request)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=400)

        m = match_stats(coop)
        parcels = Parcel.objects.filter(cooperative=coop)
        eudr = {e['eudr_status']: e['n'] for e in parcels.values('eudr_status').annotate(n=Count('id'))}
        n_parcels = sum(eudr.values())
        sheets = RegistrySheet.objects.filter(cooperative=coop).count()
        generated = Producer.objects.filter(cooperative=coop, field_id_base__regex=r'^PROD[0-9]{4,}$').count()
        agents = Agent.objects.filter(cooperative=coop, is_active=True)
        n_agents = agents.count()
        since = timezone.now() - timedelta(days=30)
        idle_agents = agents.exclude(parcels__created_at__gte=since).distinct().count() if n_agents else 0
        last_defor = AnalysisReport.objects.filter(cooperative=coop, kind='deforestation').order_by('-created_at').values_list('created_at', flat=True).first()

        key = f'coop-actions-spatial:{coop.id}'
        sp = None if request.query_params.get('refresh') else cache.get(key)
        if sp is None:
            sp = {'overlap': None, 'zones': None}
            try:
                s, _ = spatial.overlaps(coop, include_other=True, min_ha=0.01)
                sp['overlap'] = {k: s[k] for k in ('total', 'meme_producteur', 'autres_producteurs', 'autre_cooperative', 'doublons')}
                z, _ = spatial.zones(coop)
                sp['zones'] = {'loaded': z['zones_loaded'], 'in_zones': z['parcels_in_zones'], 'illegal': z['illegal']}
            except Exception:  # noqa: BLE001 — un contrôle en échec ne doit pas bloquer le tableau de bord
                pass
            sp['at'] = timezone.now().isoformat()
            cache.set(key, sp, 600)
        ov, zn = sp['overlap'] or {}, sp['zones'] or {}

        drafts = list(Lot.objects.filter(cooperative=coop, status='brouillon').order_by('-created_at')[:20])
        blocked = 0
        for lot in drafts:
            try:
                blocked += 1 if lot_checks(lot).get('blocking') else 0
            except Exception:  # noqa: BLE001
                pass
        lots_ready = Lot.objects.filter(cooperative=coop).exclude(status='brouillon').count()

        actions = []

        def add(level, key, count, title, detail, to, cta, module=None):
            if count:
                actions.append({'level': level, 'key': key, 'count': count, 'title': title, 'detail': detail,
                                'to': to, 'cta': cta, 'module': module})

        add('urgent', 'illegal', zn.get('illegal', 0), 'parcelle(s) en forêt classée ou en parc',
            'Production illégale au regard de l’EUDR : à exclure des lots ou à justifier.', '/coop/spatial?tab=zones', 'Voir les parcelles')
        add('urgent', 'overlap_producers', ov.get('autres_producteurs', 0), 'chevauchement(s) entre deux producteurs',
            'Deux producteurs déclarent la même surface : à corriger sur le terrain ou dans l’éditeur.', '/coop/spatial', 'Contrôler')
        add('urgent', 'overlap_coops', ov.get('autre_cooperative', 0), 'chevauchement(s) avec une autre coopérative',
            'Un même champ déclaré par deux coopératives : risque de double vente.', '/coop/spatial', 'Contrôler')
        add('urgent', 'non_compliant', eudr.get('non_compliant', 0), 'parcelle(s) non conforme(s)',
            'Géométrie refusée par le contrôle EUDR (surface, auto-intersection, point…).', '/coop/parcels?eudr=non_compliant', 'Corriger')
        add('urgent', 'lots_blocked', blocked, 'fiche(s) de lot bloquée(s)',
            'Un producteur sans parcelle ou une parcelle non conforme empêche la validation.', '/coop/lots', 'Ouvrir les lots', 'lots')
        add('todo', 'to_map', m['to_map'], 'producteur(s) à mapper',
            'Aucun polygone connu : ils apparaissent dans la liste des agents mappeurs.', '/coop/producers?map=todo', 'Voir la liste')
        if m['to_map'] and not n_agents:
            add('todo', 'no_agent', 1, 'aucun agent mappeur actif', 'Créez un compte agent pour lancer le mapping.', '/coop/agents', 'Créer un agent')
        add('todo', 'generated_codes', generated, 'producteur(s) avec un code généré',
            'Reprenez le code du registre pour croiser producteurs et polygones.', '/coop/producers?recode=1', 'Codes du registre')
        add('todo', 'no_code', m['polygons_without_code'], 'ancien(s) polygone(s) sans code producteur',
            'Le fichier a sans doute été importé sans son .dbf : réimportez .shp + .dbf + .prj (ou un .zip).', '/map', 'Réimporter')
        add('todo', 'orphans', max(0, m['orphan_polygons'] - m['polygons_without_code']), 'ancien(s) polygone(s) sans producteur du registre',
            'Leur code n’existe pas dans le registre : ajoutez le producteur ou corrigez le code.', '/coop/producers', 'Vérifier')
        add('todo', 'doublons', ov.get('doublons', 0), 'doublon(s) probable(s) de polygones',
            'Le même champ relevé deux fois (ancien polygone et nouveau mapping) : supprimez-en un.', '/coop/spatial', 'Voir les doublons')
        add('todo', 'pending', eudr.get('pending', 0), 'parcelle(s) en attente de contrôle',
            'Lancez la validation pour connaître leur conformité.', '/coop/parcels?eudr=pending', 'Voir les parcelles')
        if n_parcels and (not last_defor or last_defor < timezone.now() - timedelta(days=90)):
            add('todo', 'deforestation', 1, 'analyse de déforestation à (re)faire',
                'Aucune analyse depuis 90 jours : vérifiez l’absence de déforestation après 2020.', '/coop/deforestation', 'Lancer l’analyse', 'deforestation')
        add('info', 'idle_agents', idle_agents, 'agent(s) sans relevé depuis 30 jours', 'Relancez-les ou désactivez leur compte.', '/coop/agents', 'Voir les agents')

        conform = n_parcels + m['legacy_polygons']
        controls_ok = (not ov.get('autres_producteurs') and not ov.get('autre_cooperative') and not zn.get('illegal')
                       and not eudr.get('non_compliant') and sp['overlap'] is not None)
        steps = [
            {'key': 'registry', 'label': 'Registre importé', 'done': m['producers'] > 0,
             'detail': f"{m['producers']} producteur(s)" + (f' · {sheets} feuille(s)' if sheets else ''), 'to': '/coop/registry'},
            {'key': 'polygons', 'label': 'Polygones rattachés', 'done': m['producers'] > 0 and m['to_map'] == 0,
             'progress': round(100 * m['mapped_producers'] / m['producers']) if m['producers'] else 0,
             'detail': f"{m['mapped_producers']}/{m['producers']} producteur(s) cartographié(s)", 'to': '/coop/producers?map=todo'},
            {'key': 'controls', 'label': 'Contrôles passés', 'done': bool(conform) and controls_ok,
             'detail': 'Chevauchements, zones foncières, géométries', 'to': '/coop/spatial'},
            {'key': 'export', 'label': 'Prêt pour l’export', 'done': lots_ready > 0,
             'detail': f'{lots_ready} lot(s) validé(s)' if lots_ready else 'Aucun lot validé', 'to': '/coop/lots'},
        ]
        return Response({'cooperative': coop.name, 'steps': steps, 'actions': actions, 'spatial_at': sp['at'],
                         'counts': {'producers': m['producers'], 'to_map': m['to_map'], 'parcels': n_parcels,
                                    'legacy_polygons': m['legacy_polygons'], 'agents': n_agents}})
