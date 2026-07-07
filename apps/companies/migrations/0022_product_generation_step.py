from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0021_alter_productpublication_product_dna"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="generation_step",
            field=models.CharField(blank=True, max_length=80),
        ),
    ]
