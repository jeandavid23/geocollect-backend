import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra):
        if not username:
            raise ValueError('Username requis')
        user = self.model(username=username, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra):
        # Un superutilisateur Django est le propriétaire de la plateforme
        extra.setdefault('role', 'owner')
        extra.setdefault('is_staff', True)
        extra.setdefault('is_superuser', True)
        return self.create_user(username, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        # Propriétaire de la plateforme (GeoLab Service) : gère les super admins (clients) et voit tout
        OWNER = 'owner', 'Super Super Admin'
        # Client : ne voit que les coopératives qui lui sont rattachées (Cooperative.managed_by)
        SUPER_ADMIN = 'super_admin', 'Super Administrateur'
        COOPERATIVE = 'cooperative', 'Coopérative'
        AGENT = 'agent', 'Agent Mappeur'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(unique=True, blank=True, null=True)
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.AGENT)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    # Profil (stockés en base pour survivre aux redéploiements Render)
    national_id = models.CharField(max_length=100, blank=True, verbose_name="Pièce d'identité")
    photo_data = models.TextField(blank=True, verbose_name='Photo (base64)')
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    cooperative = models.ForeignKey(
        'cooperatives.Cooperative',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='users',
    )
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['full_name']

    class Meta:
        verbose_name = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.full_name} ({self.get_role_display()})'

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER

    @property
    def is_super_admin(self):
        return self.role == self.Role.SUPER_ADMIN

    @property
    def is_cooperative_user(self):
        return self.role == self.Role.COOPERATIVE

    @property
    def is_agent_user(self):
        return self.role == self.Role.AGENT


class ActivityLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='activity_logs')
    action = models.CharField(max_length=100)
    resource = models.CharField(max_length=100)
    resource_id = models.CharField(max_length=100, blank=True)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Journal d'activité"
        ordering = ['-timestamp']

    def __str__(self):
        return f'{self.user} — {self.action} — {self.timestamp}'


class Notification(models.Model):
    class Type(models.TextChoices):
        SUCCESS = 'success', 'Succès'
        INFO = 'info', 'Info'
        WARNING = 'warning', 'Avertissement'
        ERROR = 'error', 'Erreur'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    cooperative = models.ForeignKey('cooperatives.Cooperative', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='notifications')
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.INFO)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Notification'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.recipient} — {self.title}'


class AdminLicense(models.Model):
    """
    Autorisations accordées par le propriétaire de la plateforme à un super admin (un client).
    Quotas vides = illimité. Un super admin sans licence (comptes créés avant les licences) a tous les droits.
    """
    MODULES = {
        'deforestation': 'Analyse déforestation',
        'rdue': 'Matrice RDUE (forêts classées, enclaves)',
        'validator': 'Polygon Validator',
        'registry': 'Registre (tableur)',
        'legacy': 'Anciens polygones',
        'selfintersection': 'Self-intersection (nettoyage)',
        'gmr': 'Polygon & GMR (registre × polygones)',
        'lots': 'Fiches de lot',
    }

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_license')
    organization = models.CharField(max_length=255, blank=True, verbose_name='Organisation cliente')
    max_cooperatives = models.PositiveIntegerField(null=True, blank=True, verbose_name='Coopératives max.')
    max_agents_per_coop = models.PositiveIntegerField(null=True, blank=True, verbose_name='Agents max. par coopérative')
    modules = models.JSONField(default=list, blank=True, verbose_name='Modules activés')
    expires_at = models.DateField(null=True, blank=True, verbose_name="Fin d'abonnement")
    notes = models.TextField(blank=True, verbose_name='Notes internes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Licence super admin'
        verbose_name_plural = 'Licences super admin'

    def __str__(self):
        return f'Licence de {self.user}'

    @property
    def is_expired(self):
        from django.utils import timezone
        return self.expires_at is not None and self.expires_at < timezone.localdate()
