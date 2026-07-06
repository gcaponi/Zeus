from django.contrib import admin

from apps.companies.models import (
    Company,
    CompanyDNA,
    CompanyFile,
    CompanyQuestion,
    ConsistencyIssue,
    DNAFeedback,
    LLMCall,
    PipelineRun,
    Source,
)


class CompanyDNAInline(admin.StackedInline):
    model = CompanyDNA
    extra = 0
    fields = [
        "version",
        "dna_type",
        "confidence_score",
        "content",
        "is_current",
        "created_by",
        "created_at",
    ]
    readonly_fields = ["version", "confidence_score", "created_at"]
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


@admin.register(CompanyFile)
class CompanyFileAdmin(admin.ModelAdmin):
    list_display = ["original_name", "company", "file_size", "created_at"]
    search_fields = ["original_name", "company__name", "content_text"]
    readonly_fields = ["content_text", "file_size", "created_at"]


@admin.register(CompanyQuestion)
class CompanyQuestionAdmin(admin.ModelAdmin):
    list_display = [
        "code",
        "company",
        "plan_slug",
        "section_key",
        "principle",
        "answered_at",
        "created_at",
    ]
    list_filter = ["plan_slug", "section_key", "code", "principle"]
    search_fields = ["question", "answer", "company__name"]
    readonly_fields = ["question", "answer_guidance", "answer", "answered_at", "created_at"]


@admin.register(DNAFeedback)
class DNAFeedbackAdmin(admin.ModelAdmin):
    list_display = ["dna", "rating", "created_at"]
    list_filter = ["rating"]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


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


@admin.register(ConsistencyIssue)
class ConsistencyIssueAdmin(admin.ModelAdmin):
    list_display = ["title", "company", "scope", "severity", "status", "created_at"]
    list_filter = ["scope", "severity", "status"]
    search_fields = ["title", "description", "company__name", "product__name"]
    readonly_fields = ["created_at", "updated_at"]
