from django.contrib import admin

from apps.companies.models import Company, CompanyDNA, Source


class CompanyDNAInline(admin.StackedInline):
    model = CompanyDNA
    extra = 0
    fields = ["version", "content", "is_current", "created_by", "created_at"]
    readonly_fields = ["version", "created_at"]
    ordering = ["-version"]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "schema_name", "created_at"]
    search_fields = ["name", "schema_name"]
    inlines = [CompanyDNAInline]


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ["url", "company", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["url", "company__name"]
    readonly_fields = ["scraped_data", "error_msg", "created_at", "updated_at"]
