from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Plan(models.Model):
    SLUG_STARTER = "starter"
    SLUG_PROFESSIONAL = "professional"
    SLUG_ENTERPRISE = "enterprise"

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=50, unique=True)
    max_company_files = models.PositiveIntegerField(default=5)
    max_product_dnas = models.PositiveIntegerField(default=5)
    max_files_per_product = models.PositiveIntegerField(default=2)
    unlimited_company_files = models.BooleanField(default=False)
    unlimited_product_dnas = models.BooleanField(default=False)
    unlimited_files_per_product = models.BooleanField(default=False)
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
                "max_company_files": 5,
                "max_product_dnas": 5,
                "max_files_per_product": 2,
            },
            cls.SLUG_PROFESSIONAL: {
                "name": "Professional",
                "max_company_files": 15,
                "max_product_dnas": 15,
                "max_files_per_product": 5,
            },
            cls.SLUG_ENTERPRISE: {
                "name": "Legacy",
                "max_company_files": 0,
                "max_product_dnas": 0,
                "max_files_per_product": 0,
                "unlimited_company_files": True,
                "unlimited_product_dnas": True,
                "unlimited_files_per_product": True,
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

    def allows_company_file_count(self, current_count):
        return self.unlimited_company_files or current_count < self.max_company_files

    def allows_product_dna_count(self, current_count):
        return self.unlimited_product_dnas or current_count < self.max_product_dnas

    def allows_product_file_count(self, current_count):
        return self.unlimited_files_per_product or current_count < self.max_files_per_product


class Client(TenantMixin):
    name = models.CharField(max_length=100)
    paid_until = models.DateField(null=True, blank=True)
    on_trial = models.BooleanField(default=True)
    created_on = models.DateField(auto_now_add=True)
    auto_create_schema = True

    class Meta:
        ordering = ["name"]

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
    company_files_used = models.PositiveIntegerField(default=0)
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

    def can_add_company_file(self):
        return self.can_use_workspace() and self.plan.allows_company_file_count(
            self.company_files_used,
        )

    def can_add_product_dna(self):
        return self.can_use_workspace() and self.plan.allows_product_dna_count(
            self.product_dnas_used,
        )

    def can_add_product_file(self, current_product_file_count):
        return self.can_use_workspace() and self.plan.allows_product_file_count(
            current_product_file_count,
        )


class WorkspaceAccess(models.Model):
    email = models.EmailField(unique=True)
    tenant_domain = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} → {self.tenant_domain}"
