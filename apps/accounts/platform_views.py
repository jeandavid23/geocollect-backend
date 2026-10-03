"""
Espace du propriétaire de la plateforme (Super Super Admin) : /api/v1/platform/

  GET    overview/                    chiffres de toute la plateforme + un résumé par client
  GET    admins/                      super admins (clients) avec licence et consommation
  POST   admins/                      crée un super admin + son compte (identifiants renvoyés une fois)
  GET    admins/<id>/                 détail
  PATCH  admins/<id>/                 modifie l'identité, la licence (quotas, modules, échéance), la suspension
  DELETE admins/<id>/                 supprime (refusé s'il gère encore des coopératives)
  POST   cooperatives/<id>/assign/    rattache une coopérative à un super admin (ou au propriétaire : null)
"""
from django.db import transaction
from django.db.models import Count, Sum
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cooperatives.models import Cooperative
from apps.parcels.models import Parcel
from apps.producers.models import Agent, Producer

from .credentials import generate_password, generate_username
from .models import ActivityLog, AdminLicense, User
from .permissions import IsOwner
from .serializers import license_summary
from .tenancy import invalidate_tenant_cache


def admin_payload(admin):
    return {
        'id': str(admin.id),
        'username': admin.username,
        'full_name': admin.full_name,
        'email': admin.email or '',
        'phone': admin.phone,
        'is_active': admin.is_active,
        'created_at': admin.created_at.isoformat(),
        'last_login': admin.last_login.isoformat() if admin.last_login else None,
        'license': license_summary(admin),
        'notes': admin.admin_license.notes if _has_license(admin) else '',
    }


def _has_license(admin):
    try:
        admin.admin_license
        return True
    except Exception:  # noqa: BLE001
        return False


class LicenseFieldsMixin(serializers.Serializer):
    organization = serializers.CharField(required=False, allow_blank=True, max_length=255)
    max_cooperatives = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    max_agents_per_coop = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    modules = serializers.ListField(child=serializers.CharField(), required=False)
    expires_at = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_modules(self, value):
        unknown = [m for m in value if m not in AdminLicense.MODULES]
        if unknown:
            raise serializers.ValidationError(f'Modules inconnus : {", ".join(unknown)}')
        return sorted(set(value))


LICENSE_FIELDS = ['organization', 'max_cooperatives', 'max_agents_per_coop', 'modules', 'expires_at', 'notes']


class AdminCreateSerializer(LicenseFieldsMixin):
    full_name = serializers.CharField(max_length=255)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    username = serializers.CharField(required=False, allow_blank=True, max_length=150)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)

    def validate_email(self, value):
        value = (value or '').strip()
        if value and User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Cette adresse e-mail est déjà utilisée.')
        return value or None

    def validate_username(self, value):
        value = (value or '').strip()
        if value and User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('Cet identifiant est déjà pris.')
        return value

    def validate_password(self, value):
        if value:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                validate_password(value)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(list(exc.messages))
        return value


class AdminUpdateSerializer(LicenseFieldsMixin):
    full_name = serializers.CharField(required=False, max_length=255)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    is_active = serializers.BooleanField(required=False)


def _log(request, action, target_id, details=''):
    ActivityLog.objects.create(user=request.user, action=action, resource='super_admin',
                               resource_id=str(target_id), details=details,
                               ip_address=request.META.get('REMOTE_ADDR'))


class PlatformOverviewView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        admins = User.objects.filter(role='super_admin').order_by('full_name')
        per_admin = {
            row['managed_by']: row for row in Cooperative.objects.values('managed_by').annotate(
                coops=Count('id', distinct=True))
        }
        parcels_by_admin = {
            row['cooperative__managed_by']: row for row in Parcel.objects.values('cooperative__managed_by').annotate(
                parcels=Count('id'), ha=Sum('area_hectares'))
        }
        clients = []
        for a in admins:
            clients.append({
                **admin_payload(a),
                'parcels': parcels_by_admin.get(a.id, {}).get('parcels', 0),
                'hectares': round(parcels_by_admin.get(a.id, {}).get('ha') or 0, 2),
            })
        unassigned = Cooperative.objects.filter(managed_by__isnull=True).count()
        return Response({
            'totals': {
                'super_admins': admins.count(),
                'super_admins_active': admins.filter(is_active=True).count(),
                'cooperatives': Cooperative.objects.count(),
                'cooperatives_unassigned': unassigned,
                'agents': Agent.objects.count(),
                'producers': Producer.objects.count(),
                'parcels': Parcel.objects.count(),
                'hectares': round(Parcel.objects.aggregate(s=Sum('area_hectares'))['s'] or 0, 2),
                'users': User.objects.count(),
            },
            'clients': clients,
            'modules': [{'id': k, 'label': v} for k, v in AdminLicense.MODULES.items()],
        })


class PlatformAdminListView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        admins = User.objects.filter(role='super_admin').order_by('full_name')
        return Response([admin_payload(a) for a in admins])

    def post(self, request):
        s = AdminCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        username = d.get('username') or generate_username(d['full_name'])
        password = d.get('password') or generate_password()
        with transaction.atomic():
            admin = User(username=username, full_name=d['full_name'], email=d.get('email'),
                         phone=d.get('phone', ''), role=User.Role.SUPER_ADMIN,
                         # jamais d'accès à l'administration Django : elle contournerait le cloisonnement
                         is_staff=False, is_superuser=False)
            admin.set_password(password)
            admin.save()
            AdminLicense.objects.create(user=admin, **{k: d[k] for k in LICENSE_FIELDS if k in d})
        _log(request, 'create_super_admin', admin.id, d.get('organization', ''))
        try:
            from .emails import send_credentials_email
            send_credentials_email(to_email=admin.email, full_name=admin.full_name, role_label='Super Administrateur',
                                   username=username, password=password)
        except Exception:  # noqa: BLE001 — l'envoi d'e-mail est facultatif
            pass
        return Response({**admin_payload(admin), 'account_username': username, 'account_password': password},
                        status=status.HTTP_201_CREATED)


class PlatformAdminDetailView(APIView):
    permission_classes = [IsOwner]

    def _get(self, pk):
        try:
            return User.objects.get(pk=pk, role='super_admin')
        except (User.DoesNotExist, ValueError, Exception):  # noqa: BLE001 — identifiant mal formé
            return None

    def get(self, request, pk):
        admin = self._get(pk)
        if admin is None:
            return Response({'detail': 'Super admin introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        coops = admin.managed_cooperatives.annotate(n_agents=Count('agents', distinct=True)).values(
            'id', 'name', 'region', 'is_active', 'n_agents')
        return Response({**admin_payload(admin), 'cooperatives': [
            {**c, 'id': str(c['id'])} for c in coops]})

    def patch(self, request, pk):
        admin = self._get(pk)
        if admin is None:
            return Response({'detail': 'Super admin introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        s = AdminUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if 'email' in d:
            email = (d['email'] or '').strip() or None
            if email and User.objects.filter(email__iexact=email).exclude(pk=admin.pk).exists():
                return Response({'email': ['Cette adresse e-mail est déjà utilisée.']}, status=status.HTTP_400_BAD_REQUEST)
            admin.email = email
        for f in ('full_name', 'phone', 'is_active'):
            if f in d:
                setattr(admin, f, d[f])
        with transaction.atomic():
            admin.save()
            lic, _ = AdminLicense.objects.get_or_create(user=admin)
            for f in LICENSE_FIELDS:
                if f in d:
                    setattr(lic, f, d[f])
            lic.save()
        invalidate_tenant_cache()
        if 'is_active' in d:
            _log(request, 'activate_super_admin' if d['is_active'] else 'suspend_super_admin', admin.id)
        else:
            _log(request, 'update_super_admin', admin.id)
        return Response(admin_payload(admin))

    def delete(self, request, pk):
        admin = self._get(pk)
        if admin is None:
            return Response({'detail': 'Super admin introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        n = admin.managed_cooperatives.count()
        if n:
            return Response({'detail': f'Ce super admin gère encore {n} coopérative(s). Transférez-les '
                                       '(ou suspendez le compte) avant de le supprimer.'},
                            status=status.HTTP_400_BAD_REQUEST)
        _log(request, 'delete_super_admin', admin.id, admin.full_name)
        admin.delete()
        invalidate_tenant_cache()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PlatformAssignCooperativeView(APIView):
    """POST {managed_by: <uuid du super admin> | null} : transfère une coopérative à un autre client."""
    permission_classes = [IsOwner]

    def post(self, request, pk):
        try:
            coop = Cooperative.objects.get(pk=pk)
        except Exception:  # noqa: BLE001
            return Response({'detail': 'Coopérative introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        target_id = request.data.get('managed_by')
        admin = None
        if target_id:
            try:
                admin = User.objects.get(pk=target_id, role='super_admin')
            except Exception:  # noqa: BLE001
                return Response({'detail': 'Super admin introuvable.'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                limit = admin.admin_license.max_cooperatives
            except Exception:  # noqa: BLE001
                limit = None
            if limit is not None and admin.managed_cooperatives.exclude(pk=coop.pk).count() >= limit:
                return Response({'detail': f'Ce super admin a atteint sa limite de {limit} coopérative(s). '
                                           'Augmentez son quota avant le transfert.'},
                                status=status.HTTP_400_BAD_REQUEST)
        coop.managed_by = admin
        coop.save(update_fields=['managed_by'])
        invalidate_tenant_cache()
        _log(request, 'assign_cooperative', coop.id, admin.full_name if admin else 'propriétaire')
        return Response({'id': str(coop.id), 'managed_by': str(admin.id) if admin else None,
                         'managed_by_name': (admin.full_name if admin else None)})
