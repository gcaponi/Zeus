import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        migrations.CreateModel(
            name="Company",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name="ID",
                )),
                ("schema_name", models.SlugField(
                    max_length=63, unique=True,
                    help_text="Corrisponde a Client.schema_name del tenant",
                )),
                ("name", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name_plural": "companies"},
        ),
        migrations.CreateModel(
            name="CompanyDNA",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name="ID",
                )),
                ("version", models.PositiveIntegerField()),
                ("content", models.JSONField()),
                ("is_current", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
                ("company", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="dna_versions",
                    to="companies.company",
                )),
            ],
            options={
                "verbose_name": "Company DNA",
                "verbose_name_plural": "Company DNAs",
                "ordering": ["-version"],
            },
        ),
        migrations.AddConstraint(
            model_name="companydna",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_current", True)),
                fields=("company", "is_current"),
                name="unique_current_dna_per_company",
            ),
        ),
    ]
