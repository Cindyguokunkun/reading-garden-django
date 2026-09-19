from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('reading', '0015_student_parent_password_hash')]
    operations = [
        migrations.AddField(
            model_name='profile',
            name='name_en',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='classroom',
            name='section',
            field=models.PositiveSmallIntegerField(blank=True, db_index=True, null=True),
        ),
    ]
