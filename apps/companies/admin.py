from django.contrib import admin

from apps.companies.models import Company, CompanyDNA, LLMCall, PipelineRun, Source


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


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = ["model_name", "company", "tokens_in", "tokens_out", "cost_usd", "created_at"]
    list_filter = ["model_name"]
    readonly_fields = [
        "prompt_text", "response_text", "tokens_in", "tokens_out",
        "cost_usd", "latency_ms", "created_at",
    ]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = ["id", "company", "status", "current_step", "created_at", "completed_at"]
    list_filter = ["status"]
    readonly_fields = ["status", "current_step", "error_msg", "created_at", "completed_at"]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
