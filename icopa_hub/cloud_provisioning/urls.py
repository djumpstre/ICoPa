from django.urls import path

from .api import (
    ProvisioningVMCheckAPIView,
    ProvisioningVMCheckRequestDetailAPIView,
    ProvisioningVMCreateAPIView,
    ProvisioningVMDeleteAPIView,
    ProvisioningVMDetailAPIView,
    ProvisioningVMListCreateAPIView,
    ProvisioningVMStartAPIView,
    ProvisioningVMStartRequestDetailAPIView,
    ProvisioningVMStopAPIView,
    ProvisioningVMStopRequestDetailAPIView,
    ProvisioningVMUploadAPIView,
)


urlpatterns = [
    path("vms/", ProvisioningVMListCreateAPIView.as_view(), name="cloud_vm_list_create"),
    path("vms/upload/", ProvisioningVMUploadAPIView.as_view(), name="cloud_vm_upload"),
    path("vms/<int:pk>/", ProvisioningVMDetailAPIView.as_view(), name="cloud_vm_detail"),
    path("vms/<int:pk>/create/", ProvisioningVMCreateAPIView.as_view(), name="cloud_vm_create"),
    path("vms/<int:pk>/delete/", ProvisioningVMDeleteAPIView.as_view(), name="cloud_vm_delete"),
    path("vms/<int:pk>/start/", ProvisioningVMStartAPIView.as_view(), name="cloud_vm_start"),
    path("vms/<int:pk>/stop/", ProvisioningVMStopAPIView.as_view(), name="cloud_vm_stop"),
    path("vms/<int:pk>/check/", ProvisioningVMCheckAPIView.as_view(), name="cloud_vm_check"),
    path(
        "vms/<int:pk>/check_requests/<int:check_id>/",
        ProvisioningVMCheckRequestDetailAPIView.as_view(),
        name="cloud_vm_check_detail",
    ),
    path(
        "vms/<int:pk>/start_requests/<int:start_id>/",
        ProvisioningVMStartRequestDetailAPIView.as_view(),
        name="cloud_vm_start_detail",
    ),
    path(
        "vms/<int:pk>/stop_requests/<int:stop_id>/",
        ProvisioningVMStopRequestDetailAPIView.as_view(),
        name="cloud_vm_stop_detail",
    ),
]
