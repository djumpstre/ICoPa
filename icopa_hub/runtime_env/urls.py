from django.urls import path

from .api import (
    RuntimeActionCatalogAPIView,
    RuntimeEnvironmentDeleteAPIView,
    RuntimeEnvironmentDetailAPIView,
    RuntimeEnvironmentListUploadAPIView,
    RuntimeEnvironmentYAMLAPIView,
)


urlpatterns = [
    path("", RuntimeEnvironmentListUploadAPIView.as_view(), name="runtime_env_list_upload"),
    path("actions/", RuntimeActionCatalogAPIView.as_view(), name="runtime_env_actions"),
    path("<int:pk>/", RuntimeEnvironmentDeleteAPIView.as_view(), name="runtime_env_delete"),
    path("env/<str:env_name>/", RuntimeEnvironmentDetailAPIView.as_view(), name="runtime_env_detail"),
    path("env/<str:env_name>/yaml/", RuntimeEnvironmentYAMLAPIView.as_view(), name="runtime_env_yaml"),
]
