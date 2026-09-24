from django.urls import path

from .api import (
    ProfilingComparisionListCreateAPIView,
    ProfilingExperimentDetailAPIView,
    ProfilingExperimentGenerateAPIView,
    ProfilingExperimentRunListDeleteAPIView,
    ProfilingExperimentListCreateAPIView,
    ProfilingExperimentRunCheckAPIView,
    ProfilingExperimentStartAPIView,
    ProfilingRunDetailAPIView,
    ProfilingRunListAPIView,
    ProfilingRunMetricFileAPIView,
    ProfilingRunRerunAPIView,
)


urlpatterns = [
    path("", ProfilingExperimentListCreateAPIView.as_view(), name="profiling_exp_list_create"),
    path("exp/<str:exp_name>/", ProfilingExperimentDetailAPIView.as_view(), name="profiling_exp_detail"),
    path("exp/<str:exp_name>/start/", ProfilingExperimentStartAPIView.as_view(), name="profiling_exp_start"),
    path("exp/<str:exp_name>/generate/", ProfilingExperimentGenerateAPIView.as_view(), name="profiling_exp_generate"),
    path("exp/<str:exp_name>/run_check/", ProfilingExperimentRunCheckAPIView.as_view(), name="profiling_exp_run_check"),
    path("exp/<str:exp_name>/runs/", ProfilingExperimentRunListDeleteAPIView.as_view(), name="profiling_exp_runs"),
    path("runs/", ProfilingRunListAPIView.as_view(), name="profiling_run_list"),
    path("comparisions/", ProfilingComparisionListCreateAPIView.as_view(), name="profiling_comparision_list_create"),
    path("runs/<int:run_id>/", ProfilingRunDetailAPIView.as_view(), name="profiling_run_detail"),
    path("runs/<int:run_id>/rerun/", ProfilingRunRerunAPIView.as_view(), name="profiling_run_rerun"),
    path(
        "runs/<int:run_id>/metric_files/<str:run_label>/<str:file_name>/",
        ProfilingRunMetricFileAPIView.as_view(),
        name="profiling_run_metric_file",
    ),
]
