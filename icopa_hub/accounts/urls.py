from django.urls import path

from .api import (
	UserDeleteAPIView,
	UserLoginAPIView,
	UserLogoutAPIView,
	UserAPIView,
	UserRegisterAPIView,
	UserTokenRefreshAPIView,
)


urlpatterns = [
	path("user_register/", UserRegisterAPIView.as_view(), name="user_register"),
	path("user_login/", UserLoginAPIView.as_view(), name="user_login"),
	path("user/", UserAPIView.as_view(), name="user"),
	path("user_logout/", UserLogoutAPIView.as_view(), name="user_logout"),
	path("token_refresh/", UserTokenRefreshAPIView.as_view(), name="token_refresh"),
	path("user_delete/", UserDeleteAPIView.as_view(), name="user_delete"),
]
