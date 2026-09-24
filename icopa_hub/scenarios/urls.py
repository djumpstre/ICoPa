from django.urls import path

from .api import ScenarioDetailAPIView, ScenarioListUploadAPIView, ScenarioValidateAPIView


urlpatterns = [
    path("", ScenarioListUploadAPIView.as_view(), name="scenario_list_upload"),
    path("scene/<str:scenario_name>/", ScenarioDetailAPIView.as_view(), name="scenario_detail"),
    path("scene/<str:scenario_name>/validate/", ScenarioValidateAPIView.as_view(), name="scenario_validate"),
]
