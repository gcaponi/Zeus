from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_loginhandoff"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="client",
            options={
                "ordering": ["name"],
                "permissions": [
                    ("view_all_tenant_data", "Can view global tenant data"),
                    ("manage_tenant_billing", "Can manage tenant billing and status"),
                    ("delete_tenant_data", "Can delete tenant data"),
                    (
                        "reset_tenant_owner_password",
                        "Can reset a tenant owner password",
                    ),
                ],
            },
        ),
    ]
