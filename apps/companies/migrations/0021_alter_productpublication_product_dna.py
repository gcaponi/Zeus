import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0020_productpublication_product_updating"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productpublication",
            name="product_dna",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="publications",
                to="companies.productdna",
            ),
        ),
    ]
