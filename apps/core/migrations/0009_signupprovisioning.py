from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_client_security_permissions"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignupProvisioning",
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
                ("slug", models.SlugField(max_length=63, unique=True)),
                ("email", models.EmailField(max_length=254)),
                ("client_ip_hash", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        max_length=20,
                    ),
                ),
                ("error_code", models.CharField(blank=True, max_length=100)),
                ("cleanup_required", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
            ],
        ),
    ]
