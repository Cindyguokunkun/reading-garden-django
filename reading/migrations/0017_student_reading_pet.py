from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('reading', '0016_profile_name_en_classroom_section')]

    operations = [
        migrations.AddField(
            model_name='student', name='pet_kind',
            field=models.CharField(blank=True, max_length=24),
        ),
        migrations.AddField(
            model_name='student', name='pet_adopted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
