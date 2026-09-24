from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="sshcredential",
            old_name="username",
            new_name="user_name",
        ),
    ]
