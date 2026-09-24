from django.urls import path

from .api import (
    LookupAPIView, MetricsAPIView, ProfilingLookupAPIView,
    SessionDetailAPIView, SessionListCreateAPIView, SessionStopAPIView,
)

urlpatterns = [
    path("sessions/", SessionListCreateAPIView.as_view(), name="probe_sessions"),
    path("sessions/<int:pk>/", SessionDetailAPIView.as_view(), name="probe_session_detail"),
    path("sessions/<int:pk>/stop/", SessionStopAPIView.as_view(), name="probe_session_stop"),
    path("lookup/", LookupAPIView.as_view(), name="probe_lookup"),
    path("lookup/profiling/", ProfilingLookupAPIView.as_view(), name="profiling_lookup"),
    path("metrics/", MetricsAPIView.as_view(), name="probe_metrics"),
]
