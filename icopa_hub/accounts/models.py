"""
Model for user accounts.
"""

# Django
from datetime import datetime, timezone
from django.utils.translation import gettext_lazy as _
from django.db import models
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver


def user_photo_path(instance, filename):
    """
    Provide the image storage path for the ImageField.
    Args:
        - instance: the instance of the model where the ImageField is defined.
        - filename: The file name that was originally given to the uploaded file. 
                    This may not be taken into account in the destination path.
    """
    return f'user_{instance.user.id}/{filename}'


User = get_user_model()


class UserProfile(models.Model):
    """
    Model to hold additional information for a User.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    biograph = models.TextField(
        max_length=200,
        blank=True,
    )

    location = models.CharField(
        max_length=200,
        blank=True,
    )

    birth_data = models.DateField(
        null=True,
        blank=True,
    )

    display_name = models.CharField(
        max_length=200,
        blank=True
    )

    photo = models.ImageField(
        upload_to=user_photo_path,
        blank=True,
        null=True,
    )

    class Meta: 
        verbose_name = _('User Profile')
        verbose_name_plural = _('User Profiles')

    def __str__(self):
        return f'Profile of User: {self.user}'


class UserToken(models.Model):
    class TokenType(models.TextChoices):
        REFRESH = "REFRESH", "Refresh"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="tokens",
    )
    token_type = models.CharField(max_length=20, choices=TokenType.choices)
    jti = models.CharField(max_length=255, unique=True)
    token = models.TextField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    revoked = models.BooleanField(default=False)

    @classmethod
    def record_refresh(cls, user, refresh):
        return cls.objects.create(
            user=user,
            token_type=cls.TokenType.REFRESH,
            jti=str(refresh["jti"]),
            token=str(refresh),
            expires_at=datetime.fromtimestamp(int(refresh["exp"]), tz=timezone.utc),
        )

    class Meta:
        verbose_name = _("User Token")
        verbose_name_plural = _("User Tokens")

    def __str__(self):
        return f"{self.user} - {self.token_type}"



@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """
    Create or update UserProfile automatically when a User instance is created or updated. 
    """
    if created:
        UserProfile.objects.create(user=instance)
    else:
        instance.profile.save()
