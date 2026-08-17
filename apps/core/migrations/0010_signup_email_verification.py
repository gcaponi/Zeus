from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_signupprovisioning"),
    ]

    operations = [
        migrations.AddField(
            model_name="signupprovisioning",
            name="company_name",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="signupprovisioning",
            name="password_hash",
            field=models.CharField(blank=True, max_length=256),
        ),
        migrations.AddField(
            model_name="signupprovisioning",
            name="token_hash",
            field=models.CharField(
                blank=True,
                max_length=64,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="signupprovisioning",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="signupprovisioning",
            name="verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
