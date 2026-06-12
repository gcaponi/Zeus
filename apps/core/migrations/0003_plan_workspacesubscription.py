import django.db.models.deletion
from django.db import migrations, models


def seed_plans_and_subscriptions(apps, _schema_editor):
    Plan = apps.get_model("core", "Plan")
    Client = apps.get_model("core", "Client")
    WorkspaceSubscription = apps.get_model("core", "WorkspaceSubscription")

    plans = {
        "starter": {
            "name": "Starter",
            "max_company_files": 5,
            "max_product_dnas": 5,
            "max_files_per_product": 2,
            "unlimited_company_files": False,
            "unlimited_product_dnas": False,
            "unlimited_files_per_product": False,
            "is_active": True,
        },
        "professional": {
            "name": "Professional",
            "max_company_files": 15,
            "max_product_dnas": 15,
            "max_files_per_product": 5,
            "unlimited_company_files": False,
            "unlimited_product_dnas": False,
            "unlimited_files_per_product": False,
            "is_active": True,
        },
        "enterprise": {
            "name": "Enterprise",
            "max_company_files": 0,
            "max_product_dnas": 0,
            "max_files_per_product": 0,
            "unlimited_company_files": True,
            "unlimited_product_dnas": True,
            "unlimited_files_per_product": True,
            "is_active": True,
        },
    }

    for slug, defaults in plans.items():
        Plan.objects.update_or_create(slug=slug, defaults=defaults)

    starter = Plan.objects.get(slug="starter")
    for client in Client.objects.all():
        WorkspaceSubscription.objects.get_or_create(
            client=client,
            defaults={"plan": starter},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_workspaceaccess"),
    ]

    operations = [
        migrations.CreateModel(
            name="Plan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("slug", models.SlugField(max_length=50, unique=True)),
                ("max_company_files", models.PositiveIntegerField(default=5)),
                ("max_product_dnas", models.PositiveIntegerField(default=5)),
                ("max_files_per_product", models.PositiveIntegerField(default=2)),
                ("unlimited_company_files", models.BooleanField(default=False)),
                ("unlimited_product_dnas", models.BooleanField(default=False)),
                ("unlimited_files_per_product", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="WorkspaceSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("trial", "Trial"), ("active", "Active"), ("suspended", "Suspended")], default="trial", max_length=20)),
                ("company_files_used", models.PositiveIntegerField(default=0)),
                ("product_dnas_used", models.PositiveIntegerField(default=0)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("client", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="subscription", to="core.client")),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="subscriptions", to="core.plan")),
            ],
            options={
                "ordering": ["client__name"],
            },
        ),
        migrations.RunPython(seed_plans_and_subscriptions, migrations.RunPython.noop),
    ]
