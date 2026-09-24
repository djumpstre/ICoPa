"""APIs for cloud VM provisioning and lifecycle management."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from .celery_tasks import (
    check_provisioning_vm_task,
    deprovision_vm_task,
    provision_vm_task,
    start_provisioning_vm_task,
    stop_provisioning_vm_task,
)
from .models import ProvisioningVM, ProvisioningVMCheckRequest, ProvisioningVMStartRequest, ProvisioningVMStopRequest
from .serializers import (
    ProvisioningVMCheckRequestSerializer,
    ProvisioningVMStartRequestSerializer,
    ProvisioningVMStopRequestSerializer,
    ProvisioningVMSerializer,
    ProvisioningVMUploadSerializer,
)


def _validate_provider(provider: str) -> str:
    normalized = str(provider or "").strip().upper()
    if not normalized:
        raise ValidationError({"provider": "provider is required."})
    if normalized != ProvisioningVM.Provider.AZURE:
        raise ValidationError({"provider": f"Provider '{normalized}' will support soon."})
    return normalized


class ProvisioningVMListCreateAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        provider = request.query_params.get("provider", "")
        queryset = ProvisioningVM.objects.filter(created_by=request.user)
        if provider:
            queryset = queryset.filter(provider=_validate_provider(provider))
        queryset = queryset.order_by("-created_at")
        return Response(ProvisioningVMSerializer(queryset, many=True).data, status=status.HTTP_200_OK)

class ProvisioningVMDetailAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk: int, *args, **kwargs):
        provisioning_vm = get_object_or_404(ProvisioningVM, id=pk, created_by=request.user)
        return Response(ProvisioningVMSerializer(provisioning_vm).data, status=status.HTTP_200_OK)


class ProvisioningVMUploadAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ProvisioningVMUploadSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        provider = serializer.validated_data["provider"]
        group_name = serializer.validated_data["group_name"]
        raw_template = serializer.validated_data["raw_template"]
        parsed_specs: list[dict] = serializer.validated_data["parsed_specs"]
        parsed_ssh_credentials = serializer.validated_data["parsed_ssh_credentials"]

        created: list[ProvisioningVM] = []
        try:
            with transaction.atomic():
                for spec, ssh_credential in zip(parsed_specs, parsed_ssh_credentials, strict=True):
                    vm_name = str(spec.get("vm_name") or "cloud-vm")
                    provisioning_vm = ProvisioningVM.objects.create(
                        name=vm_name,
                        group_name=group_name,
                        provider=provider,
                        status=ProvisioningVM.Status.INITIALIZED,
                        raw_template=raw_template,
                        request_spec=spec,
                        icopa_config=spec.get("icopa_config") if isinstance(spec.get("icopa_config"), dict) else {},
                        ssh_credential=ssh_credential,
                        created_by=user,
                        modified_by=user,
                    )
                    created.append(provisioning_vm)
        except IntegrityError:
            group_name = str((raw_template.get("metadata") or {}).get("group_name") or "default").strip() or "default"
            vm_names = ", ".join(str(spec.get("vm_name") or "cloud-vm") for spec in parsed_specs)
            return Response(
                {
                    "detail": (
                        f"Upload rejected for group '{group_name}': duplicate VM name conflict detected "
                        f"({vm_names}). Delete existing records or rename VMs."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "provider": provider,
                "total_created": len(created),
                "vms": ProvisioningVMSerializer(created, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ProvisioningVMCreateAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk: int, *args, **kwargs):
        provisioning_vm = get_object_or_404(ProvisioningVM, id=pk, created_by=request.user)
        if provisioning_vm.provider != ProvisioningVM.Provider.AZURE:
            return Response(
                {"detail": f"Provider '{provisioning_vm.provider}' will support soon."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if provisioning_vm.status in (ProvisioningVM.Status.PENDING, ProvisioningVM.Status.RUNNING, ProvisioningVM.Status.DELETING):
            return Response(
                {"detail": f"Cannot start create while status is {provisioning_vm.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if provisioning_vm.status == ProvisioningVM.Status.SUCCEEDED:
            return Response({"detail": "Provisioning VM is already created."}, status=status.HTTP_400_BAD_REQUEST)
        if provisioning_vm.status == ProvisioningVM.Status.DELETED:
            return Response({"detail": "Provisioning VM is deleted and cannot be created."}, status=status.HTTP_400_BAD_REQUEST)

        provisioning_vm.update_execution_status(
            status=ProvisioningVM.Status.PENDING,
            modified_by_id=request.user.id,
            last_error="",
        )
        task = provision_vm_task.delay(provisioning_vm_id=provisioning_vm.id, user_id=request.user.id)
        provisioning_vm.task_id = task.id or ""
        provisioning_vm.save(update_fields=["task_id", "updated_at"])
        return Response(ProvisioningVMSerializer(provisioning_vm).data, status=status.HTTP_202_ACCEPTED)


class ProvisioningVMDeleteAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk: int, *args, **kwargs):
        provisioning_vm = get_object_or_404(ProvisioningVM, id=pk, created_by=request.user)
        if provisioning_vm.provider != ProvisioningVM.Provider.AZURE:
            return Response(
                {"detail": f"Provider '{provisioning_vm.provider}' will support soon."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if provisioning_vm.status in (
            ProvisioningVM.Status.PENDING,
            ProvisioningVM.Status.RUNNING,
            ProvisioningVM.Status.DELETING,
        ):
            return Response(
                {"detail": f"Cannot delete provisioning VM while status is {provisioning_vm.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if provisioning_vm.status == ProvisioningVM.Status.DELETED:
            return Response({"detail": "Provisioning VM is already deleted."}, status=status.HTTP_400_BAD_REQUEST)
        if provisioning_vm.status == ProvisioningVM.Status.INITIALIZED:
            provisioning_vm.update_execution_status(
                status=ProvisioningVM.Status.DELETED,
                modified_by_id=request.user.id,
                last_error="",
            )
            return Response(ProvisioningVMSerializer(provisioning_vm).data, status=status.HTTP_200_OK)

        provisioning_vm.update_execution_status(
            status=ProvisioningVM.Status.DELETING,
            modified_by_id=request.user.id,
            last_error="",
        )
        task = deprovision_vm_task.delay(provisioning_vm_id=provisioning_vm.id, user_id=request.user.id)
        provisioning_vm.task_id = task.id or ""
        provisioning_vm.save(update_fields=["task_id", "updated_at"])
        return Response(ProvisioningVMSerializer(provisioning_vm).data, status=status.HTTP_202_ACCEPTED)


class ProvisioningVMCheckAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk: int, *args, **kwargs):
        provisioning_vm = get_object_or_404(ProvisioningVM, id=pk, created_by=request.user)
        if provisioning_vm.provider != ProvisioningVM.Provider.AZURE:
            return Response(
                {"detail": f"Provider '{provisioning_vm.provider}' will support soon."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        check_request = ProvisioningVMCheckRequest.objects.create(
            provisioning_vm=provisioning_vm,
            status=ProvisioningVMCheckRequest.Status.PENDING,
            created_by=request.user,
            modified_by=request.user,
        )
        task = check_provisioning_vm_task.delay(
            check_request_id=check_request.id,
            provisioning_vm_id=provisioning_vm.id,
            user_id=request.user.id,
        )
        check_request.task_id = task.id or ""
        check_request.save(update_fields=["task_id", "updated_at"])
        return Response(
            {
                "provisioning_vm_id": provisioning_vm.id,
                "check_request_id": check_request.id,
                "status": check_request.status,
                "task_id": check_request.task_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ProvisioningVMCheckRequestDetailAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk: int, check_id: int, *args, **kwargs):
        provisioning_vm = get_object_or_404(ProvisioningVM, id=pk, created_by=request.user)
        check_request = get_object_or_404(
            ProvisioningVMCheckRequest,
            id=check_id,
            provisioning_vm=provisioning_vm,
            created_by=request.user,
        )
        payload = ProvisioningVMCheckRequestSerializer(check_request).data
        payload["provisioning_vm_id"] = provisioning_vm.id
        payload["provisioning_vm_status"] = provisioning_vm.status
        return Response(payload, status=status.HTTP_200_OK)


class ProvisioningVMStopAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk: int, *args, **kwargs):
        provisioning_vm = get_object_or_404(ProvisioningVM, id=pk, created_by=request.user)
        if provisioning_vm.provider != ProvisioningVM.Provider.AZURE:
            return Response(
                {"detail": f"Provider '{provisioning_vm.provider}' will support soon."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if provisioning_vm.status in (
            ProvisioningVM.Status.INITIALIZED,
            ProvisioningVM.Status.PENDING,
            ProvisioningVM.Status.RUNNING,
            ProvisioningVM.Status.DELETING,
            ProvisioningVM.Status.DELETED,
        ):
            return Response(
                {"detail": f"Cannot stop VM while status is {provisioning_vm.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        stop_request = ProvisioningVMStopRequest.objects.create(
            provisioning_vm=provisioning_vm,
            status=ProvisioningVMStopRequest.Status.PENDING,
            created_by=request.user,
            modified_by=request.user,
        )
        task = stop_provisioning_vm_task.delay(
            stop_request_id=stop_request.id,
            provisioning_vm_id=provisioning_vm.id,
            user_id=request.user.id,
        )
        stop_request.task_id = task.id or ""
        stop_request.save(update_fields=["task_id", "updated_at"])
        return Response(
            {
                "provisioning_vm_id": provisioning_vm.id,
                "stop_request_id": stop_request.id,
                "status": stop_request.status,
                "task_id": stop_request.task_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ProvisioningVMStopRequestDetailAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk: int, stop_id: int, *args, **kwargs):
        provisioning_vm = get_object_or_404(ProvisioningVM, id=pk, created_by=request.user)
        stop_request = get_object_or_404(
            ProvisioningVMStopRequest,
            id=stop_id,
            provisioning_vm=provisioning_vm,
            created_by=request.user,
        )
        payload = ProvisioningVMStopRequestSerializer(stop_request).data
        payload["provisioning_vm_id"] = provisioning_vm.id
        payload["provisioning_vm_status"] = provisioning_vm.status
        return Response(payload, status=status.HTTP_200_OK)


class ProvisioningVMStartAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk: int, *args, **kwargs):
        provisioning_vm = get_object_or_404(ProvisioningVM, id=pk, created_by=request.user)
        if provisioning_vm.provider != ProvisioningVM.Provider.AZURE:
            return Response(
                {"detail": f"Provider '{provisioning_vm.provider}' will support soon."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if provisioning_vm.status in (
            ProvisioningVM.Status.INITIALIZED,
            ProvisioningVM.Status.PENDING,
            ProvisioningVM.Status.RUNNING,
            ProvisioningVM.Status.DELETING,
            ProvisioningVM.Status.DELETED,
        ):
            return Response(
                {"detail": f"Cannot start VM while status is {provisioning_vm.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        start_request = ProvisioningVMStartRequest.objects.create(
            provisioning_vm=provisioning_vm,
            status=ProvisioningVMStartRequest.Status.PENDING,
            created_by=request.user,
            modified_by=request.user,
        )
        task = start_provisioning_vm_task.delay(
            start_request_id=start_request.id,
            provisioning_vm_id=provisioning_vm.id,
            user_id=request.user.id,
        )
        start_request.task_id = task.id or ""
        start_request.save(update_fields=["task_id", "updated_at"])
        return Response(
            {
                "provisioning_vm_id": provisioning_vm.id,
                "start_request_id": start_request.id,
                "status": start_request.status,
                "task_id": start_request.task_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ProvisioningVMStartRequestDetailAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk: int, start_id: int, *args, **kwargs):
        provisioning_vm = get_object_or_404(ProvisioningVM, id=pk, created_by=request.user)
        start_request = get_object_or_404(
            ProvisioningVMStartRequest,
            id=start_id,
            provisioning_vm=provisioning_vm,
            created_by=request.user,
        )
        payload = ProvisioningVMStartRequestSerializer(start_request).data
        payload["provisioning_vm_id"] = provisioning_vm.id
        payload["provisioning_vm_status"] = provisioning_vm.status
        return Response(payload, status=status.HTTP_200_OK)
