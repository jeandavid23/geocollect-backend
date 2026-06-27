from django.db.models import Count, Sum, Avg
from django.utils import timezone
from datetime import timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions

from apps.cooperatives.models import Cooperative
from apps.producers.models import Producer, Agent
from apps.parcels.models import Parcel
from apps.accounts.permissions import IsAgentOrAbove, IsCooperativeOrAdmin, IsSuperAdmin


class AdminDashboardView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        today = timezone.now().date()
        week_ago = today - timedelta(days=7)

        coop_count = Cooperative.objects.filter(is_active=True).count()
        producer_count = Producer.objects.filter(is_active=True).count()
        parcel_count = Parcel.objects.count()
        agent_count = Agent.objects.filter(is_active=True).count()
        total_ha = Parcel.objects.aggregate(t=Sum('area_hectares'))['t'] or 0

        eudr = Parcel.objects.values('eudr_status').annotate(count=Count('id'))
        eudr_map = {e['eudr_status']: e['count'] for e in eudr}

        # Daily progress last 14 days
        daily = []
        for i in range(13, -1, -1):
            day = today - timedelta(days=i)
            count = Parcel.objects.filter(created_at__date=day).count()
            ha = Parcel.objects.filter(created_at__date=day).aggregate(s=Sum('area_hectares'))['s'] or 0
            daily.append({'date': day.strftime('%d %b'), 'parcels': count, 'hectares': round(ha, 2)})

        cooperatives = Cooperative.objects.annotate(
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
            'avg_eudr_score': Parcel.objects.filter(eudr_score__isnull=False).aggregate(a=Avg('eudr_score'))['a'],
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
