from django.contrib import admin
from django.contrib.auth import get_user_model
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django_tenants.utils import schema_context

from apps.core.models import Client, Domain, Plan, WorkspaceAccess, WorkspaceSubscription

User = get_user_model()


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
        "company_files_bytes_used",
        "product_files_bytes_used",
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
        "change_password_link",
        "created_on",
    ]
    search_fields = ["name", "schema_name", "domains__domain"]
    list_filter = ["on_trial", "subscription__status", "subscription__plan"]
    inlines = [DomainInline, WorkspaceSubscriptionInline]
    actions = ["activate_subscriptions", "suspend_subscriptions"]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("domains").select_related(
            "subscription__plan",
        )

    def delete_model(self, request, obj):
        domains = list(obj.domains.values_list("domain", flat=True))
        WorkspaceAccess.objects.filter(tenant_domain__in=domains).delete()
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        domains = list(queryset.values_list("domains__domain", flat=True))
        WorkspaceAccess.objects.filter(tenant_domain__in=domains).delete()
        super().delete_queryset(request, queryset)

    @admin.action(description="Attiva subscription (active)")
    def activate_subscriptions(self, request, queryset):
        updated = 0
        for client in queryset:
            if hasattr(client, "subscription"):
                client.subscription.status = WorkspaceSubscription.STATUS_ACTIVE
                client.subscription.save(update_fields=["status"])
                updated += 1
        self.message_user(request, f"{updated} subscription attivate.")

    @admin.action(description="Sospendi subscription (suspended)")
    def suspend_subscriptions(self, request, queryset):
        updated = 0
        for client in queryset:
            if hasattr(client, "subscription"):
                client.subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
                client.subscription.save(update_fields=["status"])
                updated += 1
        self.message_user(request, f"{updated} subscription sospese.")

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

    @admin.display(description="Password")
    def change_password_link(self, obj):
        from django.utils.html import format_html
        return format_html(
            '<a href="{}" class="button">Cambia password</a>',
            reverse("admin:core_client_change_password", args=[obj.pk]),
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:client_id>/change-password/",
                self.admin_site.admin_view(self.change_password_view),
                name="core_client_change_password",
            ),
        ]
        return custom_urls + urls

    def change_password_view(self, request, client_id):
        client = Client.objects.get(pk=client_id)
        domain = next((d.domain for d in client.domains.all() if d.is_primary), None)
        if not domain:
            self.message_user(request, "Nessun dominio trovato per questo client.", level="error")
            return redirect("admin:core_client_changelist")

        email = WorkspaceAccess.objects.filter(tenant_domain=domain).values_list(
            "email", flat=True,
        ).first()

        if request.method == "POST":
            new_password = request.POST.get("new_password", "")
            confirm_password = request.POST.get("confirm_password", "")

            if not new_password:
                return render(request, "admin/change_password.html", {
                    "client": client,
                    "email": email,
                    "error": "Password obbligatoria.",
                })

            if new_password != confirm_password:
                return render(request, "admin/change_password.html", {
                    "client": client,
                    "email": email,
                    "error": "Le password non coincidono.",
                })

            if len(new_password) < 8:
                return render(request, "admin/change_password.html", {
                    "client": client,
                    "email": email,
                    "error": "Password troppo corta (minimo 8 caratteri).",
                })

            tenant_schema = client.schema_name
            with schema_context(tenant_schema):
                user = User.objects.filter(email__iexact=email).first()
                if not user:
                    return render(request, "admin/change_password.html", {
                        "client": client,
                        "email": email,
                        "error": "Utente non trovato nel tenant.",
                    })
                user.set_password(new_password)
                user.save()

            self.message_user(request, f"Password cambiata per {email}.")
            return redirect("admin:core_client_changelist")

        return render(request, "admin/change_password.html", {
            "client": client,
            "email": email,
        })


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
        "max_product_files_label",
        "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = ["name", "slug"]

    @admin.display(description="Company files")
    def max_company_files_label(self, obj):
        return "Illimitati" if obj.unlimited_company_files else f"{obj.max_company_files_mb} MB"

    @admin.display(description="Product DNAs")
    def max_product_dnas_label(self, obj):
        return "Illimitati" if obj.unlimited_product_dnas else obj.max_product_dnas

    @admin.display(description="Product files")
    def max_product_files_label(self, obj):
        return "Illimitati" if obj.unlimited_product_files else f"{obj.max_product_files_mb} MB"


@admin.register(WorkspaceSubscription)
class WorkspaceSubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "client",
        "plan",
        "status",
        "company_files_usage",
        "product_files_usage",
        "product_dnas_usage",
        "updated_at",
    ]
    list_filter = ["status", "plan"]
    search_fields = ["client__name", "client__schema_name"]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="Company files")
    def company_files_usage(self, obj):
        limit = "illimitati" if obj.plan.unlimited_company_files else f"{obj.plan.max_company_files_mb} MB"
        used_mb = obj.company_files_bytes_used / (1024 * 1024)
        return f"{used_mb:.1f}/{limit}"

    @admin.display(description="Product files")
    def product_files_usage(self, obj):
        limit = "illimitati" if obj.plan.unlimited_product_files else f"{obj.plan.max_product_files_mb} MB"
        used_mb = obj.product_files_bytes_used / (1024 * 1024)
        return f"{used_mb:.1f}/{limit}"

    @admin.display(description="Product DNAs")
    def product_dnas_usage(self, obj):
        limit = "illimitati" if obj.plan.unlimited_product_dnas else obj.plan.max_product_dnas
        return f"{obj.product_dnas_used}/{limit}"
