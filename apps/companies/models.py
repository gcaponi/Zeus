from django.conf import settings
from django.db import models


class Company(models.Model):
    schema_name = models.SlugField(
        max_length=63,
        unique=True,
        help_text="Corrisponde a Client.schema_name del tenant",
    )
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class CompanyDNA(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="dna_versions",
    )
    version = models.PositiveIntegerField()
    content = models.JSONField()
    is_current = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Company DNA"
        verbose_name_plural = "Company DNAs"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "is_current"],
                condition=models.Q(is_current=True),
                name="unique_current_dna_per_company",
            ),
        ]
        ordering = ["-version"]

    def __str__(self):
        return f"{self.company.name} v{self.version}"


class Source(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SCRAPING = "scraping"
    STATUS_SCRAPED = "scraped"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SCRAPING, "Scraping"),
        (STATUS_SCRAPED, "Scraped"),
        (STATUS_FAILED, "Failed"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    url = models.URLField(max_length=2048)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    scraped_data = models.JSONField(null=True, blank=True)
    error_msg = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.url} ({self.status})"


class PipelineRun(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="pipeline_runs",
    )
    source = models.ForeignKey(
        Source, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pipeline_runs",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING,
    )
    current_step = models.CharField(max_length=64, blank=True)
    error_msg = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pipeline #{self.pk} ({self.status})"


class LLMCall(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="llm_calls",
    )
    model_name = models.CharField(max_length=64)
    prompt_text = models.TextField()
    response_text = models.TextField()
    tokens_in = models.PositiveIntegerField()
    tokens_out = models.PositiveIntegerField()
    cost_usd = models.FloatField()
    latency_ms = models.PositiveIntegerField()
    source = models.ForeignKey(
        Source,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="llm_calls",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.model_name} @ {self.created_at:%H:%M}"
