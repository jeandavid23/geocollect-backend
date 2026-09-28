from django.db import models, transaction
from rest_framework import generics, filters, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from .models import Agent, Producer
from .serializers import AgentSerializer, AgentCreateSerializer, ProducerSerializer, ProducerCreateSerializer
from apps.accounts.permissions import IsSuperAdmin, IsCooperativeOrAdmin, IsAgentOrAbove, resolve_cooperative
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
        qs = Agent.objects.select_related('user', 'cooperative').order_by('created_at', 'id')
        user = self.request.user
        if user.role == 'cooperative':
            qs = qs.filter(cooperative=user.cooperative)
        elif user.role == 'agent':
            qs = qs.filter(user=user)
        return qs


class AgentDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Agent.objects.select_related('user', 'cooperative')
    serializer_class = AgentSerializer
    permission_classes = [IsCooperativeOrAdmin]


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
        qs = Producer.objects.select_related('cooperative', 'assigned_agent__user').order_by('last_name', 'first_name', 'id')
        user = self.request.user
        # Coopérative ET agent voient TOUS les producteurs de la coopérative
        if user.role in ('cooperative', 'agent') and user.cooperative_id:
            qs = qs.filter(cooperative_id=user.cooperative_id)
        return qs


class ProducerDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Producer.objects.select_related('cooperative', 'assigned_agent__user')
    permission_classes = [IsAgentOrAbove]

    def get_serializer_class(self):
        return ProducerCreateSerializer if self.request.method in ('PUT', 'PATCH') else ProducerSerializer


class ProducerBulkImportView(APIView):
    """
    POST /api/v1/producers/bulk/
    Corps : {"cooperative": "<uuid, super admin seulement>", "producers": [ {...}, ... ]}

    Crée en une requête les producteurs d'un fichier Excel. Chaque ligne peut porter
    `extra_data` (toutes les colonnes du fichier, sous leur entête d'origine).
    Les textes trop longs pour une colonne sont tronqués : la valeur complète reste dans extra_data.
    Réponse : {"created": [...producteurs...], "errors": [{"index": i, "errors": {...}}]}
    """
    permission_classes = [IsCooperativeOrAdmin]
    MAX_ROWS = 5000

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

        char_limits = {
            f.name: f.max_length for f in Producer._meta.get_fields()
            if isinstance(f, models.CharField) and f.max_length
        }

        valid, errors = [], []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append({'index': i, 'errors': {'detail': 'Ligne invalide.'}})
                continue
            row = {
                k: (v[:char_limits[k]] if isinstance(v, str) and k in char_limits else v)
                for k, v in row.items()
            }
            row['cooperative'] = str(cooperative.pk)
            serializer = ProducerCreateSerializer(data=row, context={'request': request})
            if serializer.is_valid():
                data = serializer.validated_data
                data['cooperative'] = cooperative
                valid.append(data)
            else:
                errors.append({'index': i, 'errors': serializer.errors})

        created = []
        if valid:
            with transaction.atomic():
                next_index = {}
                used = set()  # deux sections peuvent produire le même code (ex. « SECTION A » / « SECTIONA »)
                objs = []
                for data in valid:
                    section = data.get('section', '')
                    if section not in next_index:
                        next_index[section] = get_next_producer_index(cooperative.id, section)
                    base = generate_field_id_base(section, next_index[section])
                    while base in used or Producer.objects.filter(field_id_base=base).exists():
                        next_index[section] += 1
                        base = generate_field_id_base(section, next_index[section])
                    used.add(base)
                    next_index[section] += 1
                    objs.append(Producer(field_id_base=base, **data))
                created = Producer.objects.bulk_create(objs, batch_size=500)

            try:
                from apps.accounts.notify import notify_cooperative
                notify_cooperative(
                    cooperative, ntype='info',
                    title=f'Import Excel — {len(created)} producteur(s)',
                    message=f'{len(errors)} ligne(s) rejetée(s)' if errors else 'Toutes les lignes ont été enregistrées.',
                )
            except Exception:
                pass

        # Relecture pour disposer des champs calculés (parcel_count, noms liés…)
        created_qs = Producer.objects.select_related('cooperative', 'assigned_agent__user').filter(
            pk__in=[p.pk for p in created])
        return Response({
            'created': ProducerSerializer(created_qs, many=True).data,
            'errors': errors,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_400_BAD_REQUEST)
