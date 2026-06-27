from rest_framework import generics, filters, permissions
from django_filters.rest_framework import DjangoFilterBackend
from .models import Agent, Producer
from .serializers import AgentSerializer, AgentCreateSerializer, ProducerSerializer, ProducerCreateSerializer
from apps.accounts.permissions import IsSuperAdmin, IsCooperativeOrAdmin, IsAgentOrAbove


class AgentListCreateView(generics.ListCreateAPIView):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['cooperative', 'is_active']
    search_fields = ['user__full_name', 'code', 'zone']

    def get_serializer_class(self):
        # Création → crée aussi le compte de connexion de l'agent
        return AgentCreateSerializer if self.request.method == 'POST' else AgentSerializer

    def get_permissions(self):
        # La coopérative (ou l'admin) peut enregistrer ses propres agents
        return [IsCooperativeOrAdmin()]

    def get_queryset(self):
        qs = Agent.objects.select_related('user', 'cooperative')
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
        qs = Producer.objects.select_related('cooperative', 'assigned_agent__user')
        user = self.request.user
        if user.role == 'cooperative':
            qs = qs.filter(cooperative=user.cooperative)
        elif user.role == 'agent':
            agent = getattr(user, 'agent_profile', None)
            if agent:
                qs = qs.filter(assigned_agent=agent)
        return qs


class ProducerDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Producer.objects.select_related('cooperative', 'assigned_agent__user')
    permission_classes = [IsAgentOrAbove]

    def get_serializer_class(self):
        return ProducerCreateSerializer if self.request.method in ('PUT', 'PATCH') else ProducerSerializer
