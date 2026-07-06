import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("companies", "0019_consistencyissue"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="status",
            field=models.CharField(
                choices=[
                    ("bozza", "Bozza"),
                    ("in_costruzione", "In Costruzione"),
                    ("in_validazione", "In Validazione"),
                    ("updating", "Updating"),
                    ("attivo", "Attivo"),
                    ("archiviato", "Archiviato"),
                ],
                default="bozza",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="ProductPublication",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "channel",
                    models.CharField(
                        choices=[
                            ("website", "Sito web"),
                            ("ecommerce", "E-commerce"),
                            ("reserved_area", "Area riservata"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("published", "Pubblicata"), ("archived", "Archiviata")],
                        default="published",
                        max_length=12,
                    ),
                ),
                ("content_md", models.TextField()),
                ("published_at", models.DateTimeField(auto_now_add=True)),
                ("archived_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="publications",
                        to="companies.product",
                    ),
                ),
                (
                    "product_dna",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="publications",
                        to="companies.productdna",
                    ),
                ),
            ],
            options={"ordering": ["-published_at"]},
        ),
        migrations.AddConstraint(
            model_name="productpublication",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "published")),
                fields=("product", "channel", "status"),
                name="unique_published_product_channel",
            ),
        ),
    ]
