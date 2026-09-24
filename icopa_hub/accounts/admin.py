from django.contrib import admin

from .models import UserProfile, UserToken


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "display_name", "location", "birth_data")
    search_fields = ("user__username", "display_name", "location")
    list_select_related = ("user",)


@admin.register(UserToken)
class UserTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "token_type", "jti", "expires_at", "revoked", "created_at")
    search_fields = ("user__username", "jti")
    list_filter = ("token_type", "revoked", "expires_at")
    list_select_related = ("user",)
    readonly_fields = ("created_at",)
