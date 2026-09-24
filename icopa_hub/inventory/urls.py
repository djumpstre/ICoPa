from django.urls import path

from .api import (
    InventoryCheckVMAPIView,
    InventoryDeleteAPIView,
    InventoryListUploadAPIView,
    InventoryStopContainerAPIView,
    InventoryVMDetailAPIView,
)


urlpatterns = [
    path("", InventoryListUploadAPIView.as_view(), name="inventory_list_upload"),
    path("vm/<int:pk>/", InventoryDeleteAPIView.as_view(), name="inventory_delete"),
    path("vm/<str:vm_name>/", InventoryVMDetailAPIView.as_view(), name="inventory_vm_detail"),
    path("check_vm/", InventoryCheckVMAPIView.as_view(), name="inventory_check_vm"),
    path("stop_container/", InventoryStopContainerAPIView.as_view(), name="inventory_stop_container"),
]
