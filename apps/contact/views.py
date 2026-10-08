"""
POST  /api/v1/contact/demo/          formulaire public « Demander une démo » (limité, anti-robot)
GET   /api/v1/contact/demo/list/     demandes reçues (propriétaire)
PATCH /api/v1/contact/demo/<id>/     suivi : statut (propriétaire)
"""
import re

from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.accounts.notify import client_ip, notify_users, owners
from apps.accounts.permissions import IsOwner
from .models import DemoRequest


class DemoThrottle(AnonRateThrottle):
    scope = 'demo'


class DemoSerializer(serializers.ModelSerializer):
    website = serializers.CharField(required=False, allow_blank=True, write_only=True)   # piège à robots (champ caché)

    class Meta:
        model = DemoRequest
        fields = ['id', 'full_name', 'organization', 'profile', 'phone', 'email', 'producers', 'plan', 'message', 'status',
                  'created_at', 'website']
        read_only_fields = ['id', 'status', 'created_at']

    def validate_phone(self, v):
        digits = re.sub(r'\D', '', v or '')
        if len(digits) < 8:
            raise serializers.ValidationError('Numéro de téléphone incomplet.')
        return v.strip()

    def validate(self, attrs):
        if not attrs.get('email') and not attrs.get('phone'):
            raise serializers.ValidationError('Indiquez au moins un téléphone ou un e-mail.')
        return attrs


class DemoRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [DemoThrottle]

    def post(self, request):
        s = DemoSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if s.validated_data.pop('website', ''):
            return Response({'ok': True}, status=status.HTTP_201_CREATED)   # robot : on fait comme si
        d = s.save(ip_address=client_ip(request) or None)
        notify_users(owners(), ntype='success', title=f'Demande de démo — {d.organization}',
                     message=f'{d.full_name} · {DemoRequest.PROFILES.get(d.profile, d.profile)} · {d.phone}'
                             f'{" · " + d.email if d.email else ""}{f" · {d.producers} producteurs" if d.producers else ""}')
        try:
            from django.conf import settings
            from django.core.mail import send_mail
            to = [e for e in [getattr(settings, 'CONTACT_EMAIL', '')] if e]
            if to:
                send_mail(f'Demande de démo GeoCollect — {d.organization}',
                          f'{d.full_name}\n{d.organization} ({DemoRequest.PROFILES.get(d.profile)})\nTél. : {d.phone}\nE-mail : {d.email}\n'
                          f'Producteurs : {d.producers or "—"}\nFormule : {d.plan or "—"}\n\n{d.message}', None, to, fail_silently=True)
        except Exception:  # noqa: BLE001
            pass
        return Response({'ok': True}, status=status.HTTP_201_CREATED)


class DemoListView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        return Response(DemoSerializer(DemoRequest.objects.all()[:500], many=True).data)


class DemoDetailView(APIView):
    permission_classes = [IsOwner]

    def patch(self, request, pk):
        d = DemoRequest.objects.filter(pk=pk).first()
        if d is None:
            return Response({'detail': 'Demande introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        st = request.data.get('status')
        if st not in DemoRequest.STATUSES:
            return Response({'detail': 'Statut inconnu.'}, status=status.HTTP_400_BAD_REQUEST)
        d.status = st
        d.save(update_fields=['status'])
        return Response(DemoSerializer(d).data)

    def delete(self, request, pk):
        n = DemoRequest.objects.filter(pk=pk).delete()[0]
        return Response(status=status.HTTP_204_NO_CONTENT if n else status.HTTP_404_NOT_FOUND)
