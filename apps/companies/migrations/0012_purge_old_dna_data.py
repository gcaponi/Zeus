from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0011_add_pool_field_to_companyquestion"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "DELETE FROM companies_productsectionapproval;",
                "DELETE FROM companies_productquestion;",
                "DELETE FROM companies_productdna;",
                "DELETE FROM companies_sectionapproval;",
                "DELETE FROM companies_companyquestion;",
                "DELETE FROM companies_dnafeedback;",
                "DELETE FROM companies_companydna;",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
