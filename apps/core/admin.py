from django.contrib import admin

from apps.core.models import Client, Domain, Plan, WorkspaceAccess, WorkspaceSubscription


class DomainInline(admin.TabularInline):
    model = Domain
    extra = 0
    fields = ["domain", "is_primary"]


class WorkspaceSubscriptionInline(admin.StackedInline):
    model = WorkspaceSubscription
    extra = 0
    fields = [
        "plan",
        "status",
        "company_files_used",
        "product_dnas_used",
        "notes",
        "created_at",
        "updated_at",
    ]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "schema_name",
        "primary_domain",
        "owner_email",
        "plan_name",
        "subscription_status",
        "created_on",
    ]
    search_fields = ["name", "schema_name", "domains__domain"]
    list_filter = ["on_trial", "subscription__status", "subscription__plan"]
    inlines = [DomainInline, WorkspaceSubscriptionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("domains").select_related(
            "subscription__plan",
        )

    @admin.display(description="Domain")
    def primary_domain(self, obj):
        domain = next((d.domain for d in obj.domains.all() if d.is_primary), None)
        return domain or "-"

    @admin.display(description="Owner email")
    def owner_email(self, obj):
        domain = self.primary_domain(obj)
        if domain == "-":
            return "-"
        return WorkspaceAccess.objects.filter(tenant_domain=domain).values_list(
            "email",
            flat=True,
        ).first() or "-"

    @admin.display(description="Plan")
    def plan_name(self, obj):
        subscription = getattr(obj, "subscription", None)
        return subscription.plan.name if subscription else "-"

    @admin.display(description="Status")
    def subscription_status(self, obj):
        subscription = getattr(obj, "subscription", None)
        return subscription.status if subscription else "-"


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ["domain", "tenant", "is_primary"]
    list_filter = ["is_primary"]
    search_fields = ["domain", "tenant__name", "tenant__schema_name"]


@admin.register(WorkspaceAccess)
class WorkspaceAccessAdmin(admin.ModelAdmin):
    list_display = ["email", "tenant_domain", "created_at"]
    search_fields = ["email", "tenant_domain"]
    readonly_fields = ["created_at"]


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "slug",
        "max_company_files_label",
        "max_product_dnas_label",
        "max_files_per_product_label",
        "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = ["name", "slug"]

    @admin.display(description="Company files")
    def max_company_files_label(self, obj):
        return "Illimitati" if obj.unlimited_company_files else obj.max_company_files

    @admin.display(description="Product DNAs")
    def max_product_dnas_label(self, obj):
        return "Illimitati" if obj.unlimited_product_dnas else obj.max_product_dnas

    @admin.display(description="Files/product")
    def max_files_per_product_label(self, obj):
        return "Illimitati" if obj.unlimited_files_per_product else obj.max_files_per_product


@admin.register(WorkspaceSubscription)
class WorkspaceSubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "client",
        "plan",
        "status",
        "company_files_usage",
        "product_dnas_usage",
        "updated_at",
    ]
    list_filter = ["status", "plan"]
    search_fields = ["client__name", "client__schema_name"]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="Company files")
    def company_files_usage(self, obj):
        limit = "illimitati" if obj.plan.unlimited_company_files else obj.plan.max_company_files
        return f"{obj.company_files_used}/{limit}"

    @admin.display(description="Product DNAs")
    def product_dnas_usage(self, obj):
        limit = "illimitati" if obj.plan.unlimited_product_dnas else obj.plan.max_product_dnas
        return f"{obj.product_dnas_used}/{limit}"
