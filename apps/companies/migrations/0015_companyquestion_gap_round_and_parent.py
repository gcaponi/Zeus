# Generated manually for PIANO 3 — Gap Engine follow-up questions.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0014_companydna__enrichment"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="companyquestion",
            options={"ordering": ["question_round", "id"]},
        ),
        migrations.AddField(
            model_name="companyquestion",
            name="question_round",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="companyquestion",
            name="parent_question",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.CASCADE,
                related_name="follow_ups",
                to="companies.companyquestion",
            ),
        ),
    ]
