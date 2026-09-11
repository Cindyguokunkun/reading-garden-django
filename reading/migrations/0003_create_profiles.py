from django.db import migrations

def create_profiles(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Profile = apps.get_model('reading', 'Profile')
    for user in User.objects.all():
        Profile.objects.get_or_create(user=user, defaults={'role': 'manager' if user.is_superuser else 'teacher'})

def remove_profiles(apps, schema_editor):
    apps.get_model('reading', 'Profile').objects.all().delete()

class Migration(migrations.Migration):
    dependencies = [('reading', '0002_book_category_book_lexile_classroom_grade_and_more')]
    operations = [migrations.RunPython(create_profiles, remove_profiles)]
