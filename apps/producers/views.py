from django.db import models, transaction
from django.db.models import Count, Q, Sum
from rest_framework import generics, filters, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from .models import Agent, Producer
from .serializers import AgentSerializer, AgentCreateSerializer, ProducerSerializer, ProducerCreateSerializer
from apps.accounts.permissions import IsSuperAdmin, IsCooperativeOrAdmin, IsAgentOrAbove, resolve_cooperative, scope_to_cooperative, is_owner
from utils.field_id import generate_field_id_base, get_next_producer_index


class AgentListCreateView(generics.ListCreateAPIView):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['cooperative', 'is_active']
    search_fields = ['user__full_name', 'code', 'zone']

    def get_serializer_class(self):
        # Création → crée aussi le compte de connexion de l'agent
        return AgentCreateSerializer if self.request.method == 'POST' else AgentSerializer

    def get_permissions(self):
        # La coopérative (ou l'admin) peut enregistrer ses propres agents ;
        # l'agent peut lire sa propre fiche (le frontend en a besoin pour « Mes parcelles »)
        if self.request.method == 'GET':
            return [IsAgentOrAbove()]
        return [IsCooperativeOrAdmin()]

    def get_queryset(self):
        # ordre stable : indispensable pour paginer sans sauter ni dupliquer de lignes
        qs = (Agent.objects.select_related('user', 'cooperative')
              .annotate(parcel_count_annot=Count('parcels'), total_hectares_annot=Sum('parcels__area_hectares'))
              .order_by('created_at', 'id'))
        user = self.request.user
        if user.role == 'agent':
            return qs.filter(user=user)
        # propriétaire → tous ; super admin → agents de ses coopératives ; coopérative → les siens
        return scope_to_cooperative(qs, user)


class AgentDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AgentSerializer
    permission_classes = [IsCooperativeOrAdmin]

    def get_queryset(self):
        # une coopérative ne peut agir que sur SES agents
        return scope_to_cooperative(Agent.objects.select_related('user', 'cooperative'), self.request.user)

    def perform_update(self, serializer):
        # la coopérative d'un agent ne peut pas être changée par une coopérative
        # seul le propriétaire peut déplacer un agent vers une autre coopérative
        if not is_owner(self.request.user):
            serializer.validated_data.pop('cooperative', None)
        serializer.save()


class ProducerListCreateView(generics.ListCreateAPIView):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['cooperative', 'assigned_agent', 'section', 'village', 'gender', 'is_active']
    search_fields = ['first_name', 'last_name', 'field_id_base', 'village', 'phone']
    ordering_fields = ['last_name', 'created_at', 'section']

    def get_serializer_class(self):
        return ProducerCreateSerializer if self.request.method == 'POST' else ProducerSerializer

    def get_permissions(self):
        return [IsAgentOrAbove()]

    def get_queryset(self):
        qs = (Producer.objects.select_related('cooperative', 'assigned_agent__user')
              .annotate(parcel_count_annot=Count('parcels'), total_hectares_annot=Sum('parcels__area_hectares'))
              .order_by('last_name', 'first_name', 'id'))
        # Coopérative et agent : tous les producteurs de leur coopérative ; super admin : ses coopératives
        return scope_to_cooperative(qs, self.request.user)


class ProducerDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAgentOrAbove]

    def get_queryset(self):
        return scope_to_cooperative(
            Producer.objects.select_related('cooperative', 'assigned_agent__user'), self.request.user)

    def get_permissions(self):
        # suppression réservée à la coopérative et à l'admin
        if self.request.method == 'DELETE':
            return [IsCooperativeOrAdmin()]
        return [IsAgentOrAbove()]

    def get_serializer_class(self):
        return ProducerCreateSerializer if self.request.method in ('PUT', 'PATCH') else ProducerSerializer


class ProducerBulkImportView(APIView):
    """
    POST /api/v1/producers/bulk/
    Corps : {"cooperative": "<uuid, super admin seulement>", "producers": [ {...}, ... ]}

    Crée en une requête les producteurs d'un fichier Excel. Chaque ligne peut porter
    `extra_data` (toutes les colonnes du fichier, sous leur entête d'origine). Aucun champ n'est obligatoire.

    Conçu pour des milliers de lignes : le nombre de requêtes SQL ne dépend PAS du nombre de lignes
    (la base Neon est à plusieurs centaines de ms du serveur : une requête par ligne dépasserait le délai).
    Les valeurs invalides d'un champ typé (année, superficie…) sont ignorées ; elles restent dans extra_data.
    Réponse : {"created": [...producteurs...], "errors": [{"index": i, "errors": {...}}]}
    """
    permission_classes = [IsCooperativeOrAdmin]
    MAX_ROWS = 5000

    TEXT_FIELDS = [
        'first_name', 'last_name', 'phone', 'national_id', 'village', 'district', 'region', 'section',
        'country', 'national_farm_id', 'owner_first_name', 'owner_last_name', 'owner_phone',
        'owner_national_id', 'inspector_name',
    ]
    INT_FIELDS = [
        'birth_year', 'num_units', 'certification_year', 'permanent_workers', 'temporary_workers',
        'inspection_year', 'inspection_month', 'inspection_day',
    ]

    @staticmethod
    def _int(v):
        try:
            n = int(float(str(v).replace(',', '.').strip()))
        except (TypeError, ValueError):
            return None
        return n if 0 <= n <= 32767 else None

    @staticmethod
    def _decimal(v):
        try:
            n = round(float(str(v).replace(',', '.').replace(' ', '')), 2)
        except (TypeError, ValueError):
            return None
        return n if 0 <= n < 10 ** 8 else None

    def _clean(self, row, limits):
        data = {}
        for f in self.TEXT_FIELDS:
            v = row.get(f)
            if v is not None and v != '':
                data[f] = str(v).strip()[:limits[f]]
        for f in self.INT_FIELDS:
            n = self._int(row.get(f)) if row.get(f) not in (None, '') else None
            if n is not None:
                data[f] = n
        if row.get('total_area_ha') not in (None, ''):
            n = self._decimal(row['total_area_ha'])
            if n is not None:
                data['total_area_ha'] = n
        data['gender'] = 'F' if str(row.get('gender', '')).upper().startswith('F') else 'M'
        if row.get('owner_gender') in ('M', 'F'):
            data['owner_gender'] = row['owner_gender']
        data['farm_type'] = 'large' if row.get('farm_type') == 'large' else 'small'
        extra = row.get('extra_data')
        data['extra_data'] = extra if isinstance(extra, dict) else {}
        return data

    def post(self, request):
        rows = request.data.get('producers') or []
        if not isinstance(rows, list) or not rows:
            return Response({'detail': 'Aucun producteur à importer.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(rows) > self.MAX_ROWS:
            return Response({'detail': f'Maximum {self.MAX_ROWS} producteurs par envoi.'},
                            status=status.HTTP_400_BAD_REQUEST)

        cooperative = resolve_cooperative(request, request.data)
        if cooperative is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)

        limits = {f.name: f.max_length for f in Producer._meta.get_fields()
                  if isinstance(f, models.CharField) and f.max_length}
        # Un agent qui importe est rattaché d'office à ses producteurs
        agent = getattr(request.user, 'agent_profile', None) if request.user.role == 'agent' else None

        valid, errors = [], []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append({'index': i, 'errors': {'detail': 'Ligne invalide.'}})
                continue
            valid.append(self._clean(row, limits))

        created = []
        if valid:
            with transaction.atomic():
                sections = {d.get('section', '') for d in valid}
                codes = {s: generate_field_id_base(s, 0)[:-6] for s in sections}
                # 1 requête : nombre de producteurs déjà enregistrés par section
                counts = dict(
                    Producer.objects.filter(cooperative=cooperative, section__in=sections)
                    .values_list('section').annotate(n=Count('id')).values_list('section', 'n'))
                # 1 requête : FIELD ID déjà pris (toutes coopératives) pour ces préfixes
                prefix_q = Q()
                for code in set(codes.values()):
                    prefix_q |= Q(field_id_base__startswith=code)
                used = set(Producer.objects.filter(prefix_q).values_list('field_id_base', flat=True))

                next_index = {s: counts.get(s, 0) + 1 for s in sections}
                objs = []
                for data in valid:
                    section = data.get('section', '')
                    base = generate_field_id_base(section, next_index[section])
                    while base in used:
                        next_index[section] += 1
                        base = generate_field_id_base(section, next_index[section])
                    used.add(base)
                    next_index[section] += 1
                    objs.append(Producer(cooperative=cooperative, assigned_agent=agent, field_id_base=base, **data))
                created = Producer.objects.bulk_create(objs, batch_size=1000)

            try:
                from apps.accounts.notify import notify_cooperative
                notify_cooperative(
                    cooperative, ntype='info',
                    title=f'Import Excel — {len(created)} producteur(s)',
                    message=f'{len(errors)} ligne(s) rejetée(s)' if errors else 'Toutes les lignes ont été enregistrées.',
                )
            except Exception:
                pass

        # Réponse sans requête supplémentaire : les nouveaux producteurs n'ont encore aucune parcelle
        for p in created:
            p.parcel_count_annot = 0
            p.total_hectares_annot = 0
        return Response({
            'created': ProducerSerializer(created, many=True).data,
            'errors': errors,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_400_BAD_REQUEST)
