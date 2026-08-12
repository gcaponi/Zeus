import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0027_company_anagrafica_and_product_suggestions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PaidOperation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("kind", models.CharField(max_length=50)),
                ("idempotency_key", models.CharField(max_length=64)),
                ("resource_key", models.CharField(blank=True, max_length=120)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                            ("rejected", "Rejected"),
                        ],
                        default="queued",
                        max_length=20,
                    ),
                ),
                ("payload", models.JSONField(default=dict)),
                ("result", models.JSONField(default=dict)),
                ("reserved_units", models.PositiveIntegerField(default=1)),
                (
                    "actual_cost_usd",
                    models.DecimalField(decimal_places=6, default=0, max_digits=12),
                ),
                ("error_code", models.CharField(blank=True, max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="paid_operations",
                        to="companies.company",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="paidoperation",
            constraint=models.UniqueConstraint(
                fields=("company", "kind", "idempotency_key"),
                name="unique_paid_operation_idempotency",
            ),
        ),
        migrations.AddConstraint(
            model_name="paidoperation",
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(("status__in", ["queued", "running"]))
                    & ~models.Q(("resource_key", ""))
                ),
                fields=("company", "resource_key"),
                name="unique_active_paid_operation_resource",
            ),
        ),
        migrations.AddIndex(
            model_name="paidoperation",
            index=models.Index(
                fields=["company", "status", "created_at"],
                name="paidop_company_status_idx",
            ),
        ),
    ]
