from django.db import migrations, models


def rename_company_files_fields(apps, schema_editor):
    Plan = apps.get_model("core", "Plan")

    for plan in Plan.objects.all():
        if plan.slug == "starter":
            plan.max_company_files_mb = 5
        elif plan.slug == "professional":
            plan.max_company_files_mb = 10
        elif plan.slug == "enterprise":
            plan.max_company_files_mb = 15
            plan.unlimited_company_files = False
        plan.save(update_fields=["max_company_files_mb", "unlimited_company_files"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_update_plan_labels"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="max_company_files_mb",
            field=models.PositiveIntegerField(default=5),
        ),
        migrations.AddField(
            model_name="workspacesubscription",
            name="company_files_bytes_used",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.RemoveField(
            model_name="plan",
            name="max_company_files",
        ),
        migrations.RemoveField(
            model_name="workspacesubscription",
            name="company_files_used",
        ),
        migrations.RunPython(rename_company_files_fields, migrations.RunPython.noop),
    ]
