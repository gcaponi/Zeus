from django.conf import settings
from django.db import models

from apps.companies.dna_schemas import (
    LAYER_KEYS,
    LAYER_TITLES,
    PRODUCT_LAYER_KEYS,
    PRODUCT_LAYER_TITLES,
    PRODUCT_DNA_SECTION_CHOICES,
)

DNA_GENERALE_SECTION_CHOICES = [(key, LAYER_TITLES[key]) for key in LAYER_KEYS]


class Company(models.Model):
    ARCHETIPO_BENI_FISICI = "beni_fisici"
    ARCHETIPO_LAVORAZIONE = "lavorazione_materiali"
    ARCHETIPO_INSTALLAZIONE = "installazione_posa"
    ARCHETIPO_SERVIZI = "servizi_professionali"
    ARCHETIPO_SOFTWARE = "software_saas"
    ARCHETIPO_DISTRIBUZIONE = "distribuzione_commercio"
    ARCHETIPO_FORMAZIONE = "formazione_consulenza"

    ARCHETIPO_CHOICES = [
        (ARCHETIPO_BENI_FISICI, "Produzione di beni fisici"),
        (ARCHETIPO_LAVORAZIONE, "Trasformazione/lavorazione materiali"),
        (ARCHETIPO_INSTALLAZIONE, "Installazione/posa"),
        (ARCHETIPO_SERVIZI, "Servizi professionali"),
        (ARCHETIPO_SOFTWARE, "Software/SaaS"),
        (ARCHETIPO_DISTRIBUZIONE, "Distribuzione/commercio"),
        (ARCHETIPO_FORMAZIONE, "Formazione/consulenza"),
    ]

    CLIENTE_B2B_TECNICO = "b2b_tecnico"
    CLIENTE_B2B_NON_TECNICO = "b2b_non_tecnico"
    CLIENTE_B2B2C = "b2b2c"
    CLIENTE_B2C = "b2c"

    CLIENTE_CHOICES = [
        (CLIENTE_B2B_TECNICO, "B2B con professionisti tecnici"),
        (CLIENTE_B2B_NON_TECNICO, "B2B con buyer non tecnici"),
        (CLIENTE_B2B2C, "B2B2C"),
        (CLIENTE_B2C, "B2C"),
    ]

    CUSTOM_SISTEMATICO = "sistematico"
    CUSTOM_RARAMENTE = "raramente"
    CUSTOM_MAI = "mai"

    CUSTOM_CHOICES = [
        (CUSTOM_SISTEMATICO, "Sì, sistematicamente"),
        (CUSTOM_RARAMENTE, "Sì, raramente"),
        (CUSTOM_MAI, "No, mai"),
    ]

    schema_name = models.SlugField(
        max_length=63,
        unique=True,
        help_text="Corrisponde a Client.schema_name del tenant",
    )
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- Profilo Operativo (routing per generazione domande) ---
    settore_primario = models.CharField(
        max_length=30, choices=ARCHETIPO_CHOICES, blank=True, default="",
    )
    prodotto_fisico = models.BooleanField(null=True, blank=True, default=None)
    cliente_diretto = models.CharField(
        max_length=20, choices=CLIENTE_CHOICES, blank=True, default="",
    )
    custom_frequenza = models.CharField(
        max_length=15, choices=CUSTOM_CHOICES, blank=True, default="",
    )
    installatori_in_filiera = models.BooleanField(null=True, blank=True, default=None)
    settore_secondario = models.CharField(max_length=255, blank=True, default="")
    contesto_libero = models.TextField(
        blank=True, default="",
        help_text="Cosa un nuovo cliente dovrebbe capire prima di lavorare con voi, che non trova sul sito",
    )

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class DNAFeedback(models.Model):
    dna = models.ForeignKey(
        "CompanyDNA", on_delete=models.CASCADE, related_name="feedbacks",
    )
    rating = models.PositiveSmallIntegerField()  # 1-5
    comment = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Feedback {self.rating}/5 on DNA #{self.dna_id}"


class CompanyDNA(models.Model):
    TYPE_PRE = "pre"
    TYPE_COMPLETE = "complete"

    DNA_TYPE_CHOICES = [
        (TYPE_PRE, "Pre-DNA"),
        (TYPE_COMPLETE, "DNA completo"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="dna_versions",
    )
    version = models.PositiveIntegerField()
    dna_type = models.CharField(
        max_length=20,
        choices=DNA_TYPE_CHOICES,
        default=TYPE_PRE,
    )
    content = models.JSONField()
    confidence_score = models.FloatField(null=True, blank=True)
    is_current = models.BooleanField(default=True)
    is_approved = models.DateTimeField(null=True, blank=True)
    # Tamper-evident audit chain (PIANO 1.5 Task 6). audit_hash binds the
    # content to the previous version's hash; previous_hash links the chain.
    audit_hash = models.CharField(max_length=64, null=True, blank=True)
    previous_hash = models.CharField(max_length=64, null=True, blank=True)
    # Cognitive enrichment bundle (PIANO 1.5 integration): validator flags,
    # scoring metrics, evidence mismatches — all diagnostics in one JSON.
    _enrichment = models.JSONField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def recalculate_confidence(dna_id: int) -> float | None:
        """Recency-weighted average of feedback ratings."""
        feedbacks = list(DNAFeedback.objects.filter(dna_id=dna_id).order_by("-created_at"))
        if not feedbacks:
            return None
        total_weight = 0.0
        weighted_sum = 0.0
        weight = 1.0
        decay = 0.5
        for fb in feedbacks:
            weighted_sum += fb.rating * weight
            total_weight += weight
            weight *= decay
        return round(weighted_sum / total_weight, 2)

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

    def is_fully_approved(self):
        return self.is_approved is not None

    def is_export_ready(self):
        if self.dna_type != self.TYPE_COMPLETE or not self.is_fully_approved():
            return False
        # Check if all products have approved DNA
        products = self.company.products.all()
        if not products:
            return True  # No products required
        for product in products:
            product_dna = product.dna_versions.filter(is_current=True).first()
            if not product_dna or not product_dna.is_fully_approved():
                return False
        return True

    def approved_sections(self):
        return {s.section_key for s in self.section_approvals.filter(is_clarification=False)}

    def missing_sections(self):
        all_keys = set(LAYER_KEYS)
        return sorted(all_keys - self.approved_sections())


class SectionApproval(models.Model):
    SECTION_KEYS = DNA_GENERALE_SECTION_CHOICES

    dna = models.ForeignKey(
        CompanyDNA,
        on_delete=models.CASCADE,
        related_name="section_approvals",
    )
    section_key = models.CharField(max_length=20, choices=SECTION_KEYS)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(auto_now_add=True)
    comment = models.TextField(null=True, blank=True)
    is_clarification = models.BooleanField(default=False)

    class Meta:
        ordering = ["-approved_at"]

    def __str__(self):
        return f"{self.section_key} on DNA {self.dna_id}"


class CompanyFile(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="company_files",
    )
    original_name = models.CharField(max_length=255)
    content_text = models.TextField()
    file_size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name


class CompanyQuestion(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="company_questions",
    )
    dna = models.ForeignKey(
        CompanyDNA,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    code = models.CharField(max_length=4)
    plan_slug = models.CharField(max_length=20, default="starter")
    section_key = models.CharField(max_length=20, default="logica_decisionale")
    POOL_TEMPLATE = "template"
    POOL_KB_ANCHORED = "kb_anchored"
    POOL_CHOICES = [
        (POOL_TEMPLATE, "Template-anchored"),
        (POOL_KB_ANCHORED, "KB-anchored"),
    ]
    pool = models.CharField(max_length=20, choices=POOL_CHOICES, default=POOL_TEMPLATE)
    principle = models.CharField(max_length=120)
    question = models.TextField()
    answer_depth = models.CharField(max_length=40, default="generica")
    answer_guidance = models.TextField(blank=True)
    answer = models.TextField(blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    # Gap Engine support (PIANO 3): follow-up questions live in later rounds and
    # can optionally point to the parent question that triggered them.
    question_round = models.PositiveSmallIntegerField(default=1)
    parent_question = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="follow_ups",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["question_round", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["dna", "code"],
                name="unique_company_question_per_dna_code",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.company.name}"


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


class Product(models.Model):
    STATUS_BOZZA = "bozza"
    STATUS_IN_COSTRUZIONE = "in_costruzione"
    STATUS_IN_VALIDAZIONE = "in_validazione"
    STATUS_ATTIVO = "attivo"
    STATUS_ARCHIVIATO = "archiviato"

    STATUS_CHOICES = [
        (STATUS_BOZZA, "Bozza"),
        (STATUS_IN_COSTRUZIONE, "In Costruzione"),
        (STATUS_IN_VALIDAZIONE, "In Validazione"),
        (STATUS_ATTIVO, "Attivo"),
        (STATUS_ARCHIVIATO, "Archiviato"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="products",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    tipologia = models.CharField(
        max_length=100,
        blank=True,
        help_text="Es: canale, vasca, piastrini, copricanale",
    )
    codice = models.CharField(
        max_length=50,
        blank=True,
        help_text="Codice interno dello specialista, es: CI-001",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_BOZZA,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "slug"],
                name="unique_product_slug_per_company",
            ),
            models.UniqueConstraint(
                fields=["company", "codice"],
                name="unique_product_code_per_company",
            ),
        ]

    def __str__(self):
        return self.name


class ProductDNA(models.Model):
    TYPE_PRE = "pre"
    TYPE_COMPLETE = "complete"

    DNA_TYPE_CHOICES = [
        (TYPE_PRE, "Pre-DNA"),
        (TYPE_COMPLETE, "DNA completo"),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="dna_versions",
    )
    version = models.PositiveIntegerField()
    dna_type = models.CharField(
        max_length=20,
        choices=DNA_TYPE_CHOICES,
        default=TYPE_PRE,
    )
    content = models.JSONField()
    is_current = models.BooleanField(default=True)
    is_approved = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    audit_hash = models.CharField(max_length=64, null=True, blank=True)
    previous_hash = models.CharField(max_length=64, null=True, blank=True)
    _enrichment = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = "Product DNA"
        verbose_name_plural = "Product DNAs"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "is_current"],
                condition=models.Q(is_current=True),
                name="unique_current_product_dna_per_product",
            ),
        ]
        ordering = ["-version"]

    def __str__(self):
        return f"{self.product.name} v{self.version}"

    def is_fully_approved(self):
        return self.is_approved is not None

    def approved_sections(self):
        return {s.section_key for s in self.section_approvals.filter(is_clarification=False)}

    def missing_sections(self):
        all_keys = set(PRODUCT_LAYER_KEYS)
        return sorted(all_keys - self.approved_sections())


class ProductFile(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="product_files",
    )
    original_name = models.CharField(max_length=255)
    content_text = models.TextField()
    file_size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name


class ProductQuestion(models.Model):
    POOL_TEMPLATE = "template"
    POOL_KB_ANCHORED = "kb_anchored"
    POOL_CHOICES = [
        (POOL_TEMPLATE, "Template"),
        (POOL_KB_ANCHORED, "KB Anchored"),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="product_questions",
    )
    dna = models.ForeignKey(
        ProductDNA,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    code = models.CharField(max_length=4)
    plan_slug = models.CharField(max_length=20, default="starter")
    section_key = models.CharField(max_length=20, default="identita_tecnica")
    principle = models.CharField(max_length=120)
    question = models.TextField()
    answer_depth = models.CharField(max_length=40, default="generica")
    answer_guidance = models.TextField(blank=True)
    answer = models.TextField(blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    pool = models.CharField(
        max_length=20,
        choices=POOL_CHOICES,
        default=POOL_TEMPLATE,
    )
    question_round = models.PositiveSmallIntegerField(default=1)
    parent_question = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="follow_ups",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["question_round", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["dna", "code"],
                name="unique_product_question_per_dna_code",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.product.name}"


class ProductSectionApproval(models.Model):
    SECTION_KEYS = PRODUCT_DNA_SECTION_CHOICES

    dna = models.ForeignKey(
        ProductDNA,
        on_delete=models.CASCADE,
        related_name="section_approvals",
    )
    section_key = models.CharField(max_length=20, choices=SECTION_KEYS)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(auto_now_add=True)
    comment = models.TextField(null=True, blank=True)
    is_clarification = models.BooleanField(default=False)

    class Meta:
        ordering = ["-approved_at"]

    def __str__(self):
        return f"{self.section_key} on ProductDNA {self.dna_id}"


class ConsistencyIssue(models.Model):
    SCOPE_PERIODIC = "periodic"
    SCOPE_SPECIALIST = "specialist"
    SCOPE_CHOICES = [
        (SCOPE_PERIODIC, "Audit periodico"),
        (SCOPE_SPECIALIST, "Audit specialista"),
    ]

    SEVERITY_LOW = "low"
    SEVERITY_MEDIUM = "medium"
    SEVERITY_HIGH = "high"
    SEVERITY_CHOICES = [
        (SEVERITY_LOW, "Bassa"),
        (SEVERITY_MEDIUM, "Media"),
        (SEVERITY_HIGH, "Alta"),
    ]

    STATUS_OPEN = "open"
    STATUS_IGNORED = "ignored"
    STATUS_RESOLVED = "resolved"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Aperta"),
        (STATUS_IGNORED, "Ignorata"),
        (STATUS_RESOLVED, "Risolta"),
        (STATUS_ARCHIVED, "Archiviata"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="consistency_issues",
    )
    company_dna = models.ForeignKey(
        CompanyDNA,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="consistency_issues",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="consistency_issues",
    )
    product_dna = models.ForeignKey(
        ProductDNA,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="consistency_issues",
    )
    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES)
    issue_type = models.CharField(max_length=40, blank=True)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default=SEVERITY_MEDIUM)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_OPEN)
    title = models.CharField(max_length=160)
    description = models.TextField()
    recommendation = models.TextField(blank=True)
    company_layer = models.CharField(max_length=30, blank=True)
    product_layer = models.CharField(max_length=30, blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status", "scope"]),
            models.Index(fields=["company", "severity", "status"]),
        ]

    def __str__(self):
        return f"{self.get_scope_display()} - {self.title}"
