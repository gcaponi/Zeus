from django.db import migrations


def update_plan_labels(apps, schema_editor):
    Plan = apps.get_model("core", "Plan")
    labels = {
        "starter": "Foundation",
        "professional": "Professional",
        "enterprise": "Legacy",
    }
    for slug, name in labels.items():
        Plan.objects.filter(slug=slug).update(name=name)


def restore_plan_labels(apps, schema_editor):
    Plan = apps.get_model("core", "Plan")
    labels = {
        "starter": "Starter",
        "professional": "Professional",
        "enterprise": "Enterprise",
    }
    for slug, name in labels.items():
        Plan.objects.filter(slug=slug).update(name=name)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_plan_workspacesubscription"),
    ]

    operations = [
        migrations.RunPython(update_plan_labels, restore_plan_labels),
    ]
