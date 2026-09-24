from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import permissions, serializers
from rest_framework.authentication import TokenAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from prometheus_client import CONTENT_TYPE_LATEST

from icopa_core.observability.prometheus.exporter import render_probe_metrics
from profiling_exps.models import ProfilingRun
from .lookup import latest_measurements
from .models import ProbeSession
from .serializers import LookupQuerySerializer, ProbeSessionSerializer, ProbeWindowSerializer
from .tasks import dispatch_session


class OwnedAPIView(APIView):
    authentication_classes = [TokenAuthentication, JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_session(self, request, pk):
        return get_object_or_404(ProbeSession, pk=pk, created_by=request.user)


class SessionListCreateAPIView(OwnedAPIView):
    def get(self, request):
        query = LookupQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        sessions = ProbeSession.objects.filter(created_by=request.user).order_by("-pk")[:query.validated_data["limit"]]
        return Response(ProbeSessionSerializer(sessions, many=True).data)

    def post(self, request):
        serializer = ProbeSessionSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            session = serializer.save(created_by=request.user)
        except IntegrityError:
            return Response({"detail": "An active probe session already exists for this pair."}, status=409)
        queued = dispatch_session(session)
        session.refresh_from_db()
        return Response(ProbeSessionSerializer(session).data, status=202 if queued else 503)


class SessionDetailAPIView(OwnedAPIView):
    def get(self, request, pk):
        session = self.get_session(request, pk)
        return Response({
            **ProbeSessionSerializer(session).data,
            "windows": ProbeWindowSerializer(session.windows.all(), many=True).data,
        })


class SessionStopAPIView(OwnedAPIView):
    def post(self, request, pk):
        session = self.get_session(request, pk)
        session.finish(ProbeSession.Status.STOPPED)
        return Response(ProbeSessionSerializer(session).data)


class LookupAPIView(OwnedAPIView):
    def get(self, request):
        query = LookupQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response({"measurement_kind": "online_icmp", "results": latest_measurements(request.user, **query.validated_data)})


class ProfilingLookupAPIView(OwnedAPIView):
    def get(self, request):
        class Query(serializers.Serializer):
            experiment_id = serializers.IntegerField(min_value=1)
            limit = serializers.IntegerField(min_value=1, max_value=100, default=20)

        query = Query(data=request.query_params)
        query.is_valid(raise_exception=True)
        runs = ProfilingRun.objects.filter(
            requested_by=request.user, experiment__created_by=request.user,
            experiment_id=query.validated_data["experiment_id"],
            status=ProfilingRun.RunStatus.SUCCEEDED, metrics_collected=True,
        ).select_related("experiment").order_by("-finished_at", "-pk")[:query.validated_data["limit"]]
        return Response({"measurement_kind": "offline_profiling", "results": [{
            "run_id": run.pk, "experiment_id": run.experiment_id,
            "gen_version": run.gen_version, "finished_at": run.finished_at,
            "characterization_parameters": run.get_characterization_parameters(),
            "summaries": run.get_rrt_summaries(),
        } for run in runs]})


class MetricsAPIView(OwnedAPIView):
    def get(self, request):
        query = LookupQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return HttpResponse(render_probe_metrics(latest_measurements(request.user, **query.validated_data)), content_type=CONTENT_TYPE_LATEST)
