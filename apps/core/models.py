from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Plan(models.Model):
    SLUG_STARTER = "starter"
    SLUG_PROFESSIONAL = "professional"
    SLUG_ENTERPRISE = "enterprise"

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=50, unique=True)
    max_company_files_mb = models.PositiveIntegerField(default=5)
    max_product_dnas = models.PositiveIntegerField(default=5)
    max_files_per_product = models.PositiveIntegerField(default=2)
    max_product_files_mb = models.PositiveIntegerField(default=5)
    unlimited_company_files = models.BooleanField(default=False)
    unlimited_product_dnas = models.BooleanField(default=False)
    unlimited_files_per_product = models.BooleanField(default=False)
    unlimited_product_files = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def default_values(cls, slug):
        plans = {
            cls.SLUG_STARTER: {
                "name": "Foundation",
                "max_company_files_mb": 5,
                "max_product_dnas": 5,
                "max_files_per_product": 2,
                "max_product_files_mb": 5,
            },
            cls.SLUG_PROFESSIONAL: {
                "name": "Professional",
                "max_company_files_mb": 10,
                "max_product_dnas": 15,
                "max_files_per_product": 5,
                "max_product_files_mb": 10,
            },
            cls.SLUG_ENTERPRISE: {
                "name": "Legacy",
                "max_company_files_mb": 15,
                "max_product_dnas": 0,
                "max_files_per_product": 0,
                "max_product_files_mb": 15,
                "unlimited_product_dnas": True,
                "unlimited_files_per_product": True,
                "unlimited_product_files": True,
            },
        }
        return plans[slug]

    @classmethod
    def get_default(cls):
        plan, _ = cls.objects.get_or_create(
            slug=cls.SLUG_STARTER,
            defaults=cls.default_values(cls.SLUG_STARTER),
        )
        return plan

    def allows_company_file_bytes(self, current_bytes):
        return (
            self.unlimited_company_files
            or current_bytes < self.max_company_files_mb * 1024 * 1024
        )

    def allows_product_dna_count(self, current_count):
        return self.unlimited_product_dnas or current_count < self.max_product_dnas

    def allows_product_file_count(self, current_count):
        return self.unlimited_files_per_product or current_count < self.max_files_per_product

    def allows_product_file_bytes(self, current_bytes):
        return (
            self.unlimited_product_files
            or current_bytes < self.max_product_files_mb * 1024 * 1024
        )


class Client(TenantMixin):
    name = models.CharField(max_length=100)
    paid_until = models.DateField(null=True, blank=True)
    on_trial = models.BooleanField(default=True)
    created_on = models.DateField(auto_now_add=True)
    auto_create_schema = True

    class Meta:
        ordering = ["name"]
        permissions = [
            ("view_all_tenant_data", "Can view global tenant data"),
            ("manage_tenant_billing", "Can manage tenant billing and status"),
            ("delete_tenant_data", "Can delete tenant data"),
            ("reset_tenant_owner_password", "Can reset a tenant owner password"),
        ]

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    pass


class WorkspaceSubscription(models.Model):
    STATUS_TRIAL = "trial"
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"

    STATUS_CHOICES = [
        (STATUS_TRIAL, "Trial"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUSPENDED, "Suspended"),
    ]

    client = models.OneToOneField(
        Client,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_TRIAL,
    )
    company_files_bytes_used = models.PositiveBigIntegerField(default=0)
    product_files_bytes_used = models.PositiveBigIntegerField(default=0)
    product_dnas_used = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["client__name"]

    def __str__(self):
        return f"{self.client.name} - {self.plan.name} ({self.status})"

    def can_use_workspace(self):
        return self.status != self.STATUS_SUSPENDED and self.plan.is_active

    def can_add_company_file(self, additional_bytes=0):
        return self.can_use_workspace() and self.plan.allows_company_file_bytes(
            self.company_files_bytes_used + additional_bytes,
        )

    def can_add_product_dna(self):
        return self.can_use_workspace() and self.plan.allows_product_dna_count(
            self.product_dnas_used,
        )

    def can_add_product_file(self, current_product_file_count):
        return self.can_use_workspace() and self.plan.allows_product_file_count(
            current_product_file_count,
        )

    def can_add_product_file_bytes(self, additional_bytes=0):
        return self.can_use_workspace() and self.plan.allows_product_file_bytes(
            self.product_files_bytes_used + additional_bytes,
        )


class WorkspaceAccess(models.Model):
    email = models.EmailField(unique=True)
    tenant_domain = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} → {self.tenant_domain}"


class LoginHandoff(models.Model):
    """Token monouso per portare il login dal public host al tenant host.

    Nato con la rimozione di SESSION_COOKIE_DOMAIN (sessioni host-only,
    Codex Security finding 1): public_login autentica sul public host e
    redirige al tenant con un token usa-e-getta (60s); la view login_handoff
    lo consuma e apre la sessione sull'host del tenant. In tabella solo
    l'hash del token, mai il token in chiaro.
    """

    token_hash = models.CharField(max_length=64, unique=True)
    tenant_schema = models.CharField(max_length=63)
    user_id = models.BigIntegerField()
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"handoff user {self.user_id} → {self.tenant_schema}"


class SignupProvisioning(models.Model):
    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    slug = models.SlugField(max_length=63, unique=True)
    email = models.EmailField()
    client_ip_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error_code = models.CharField(max_length=100, blank=True)
    cleanup_required = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"signup {self.slug} ({self.status})"
