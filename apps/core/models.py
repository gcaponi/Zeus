from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


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


class WorkspaceAccess(models.Model):
    email = models.EmailField(unique=True)
    tenant_domain = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} → {self.tenant_domain}"
