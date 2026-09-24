import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import PowerSettingsNewIcon from "@mui/icons-material/PowerSettingsNew";
import RuleIcon from "@mui/icons-material/Rule";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { StatusBadge } from "../../../shared/components/StatusBadge";
import { formatDateTime } from "../../../shared/utils/date";
import { useCloudStore } from "../../../stores/cloud.store";

function JsonPanel({ title, payload }: { title: string; payload: unknown }) {
  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        {title}
      </Typography>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1.2,
          borderRadius: 1,
          bgcolor: "background.default",
          border: 1,
          borderColor: "divider",
          fontSize: 12,
          overflowX: "auto",
        }}
      >
        {JSON.stringify(payload ?? {}, null, 2)}
      </Box>
    </Paper>
  );
}

export function CloudVmDetailPage() {
  const params = useParams<{ vmId: string }>();
  const vmId = Number(params.vmId);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const detail = useCloudStore((state) => state.detailById[vmId]);
  const loadingDetail = useCloudStore((state) => state.loadingDetail);
  const error = useCloudStore((state) => state.error);
  const actionLoadingById = useCloudStore((state) => state.actionLoadingById);
  const fetchDetail = useCloudStore((state) => state.fetchDetail);
  const startCreate = useCloudStore((state) => state.startCreate);
  const deleteVm = useCloudStore((state) => state.deleteVm);
  const startVm = useCloudStore((state) => state.startVm);
  const stopVm = useCloudStore((state) => state.stopVm);
  const triggerCheck = useCloudStore((state) => state.triggerCheck);

  useEffect(() => {
    if (Number.isFinite(vmId)) {
      void fetchDetail(vmId);
    }
  }, [fetchDetail, vmId]);

  if (!Number.isFinite(vmId)) {
    return <EmptyState label="Invalid cloud VM id." />;
  }
  if (loadingDetail && !detail) {
    return <LoadingState label="Loading cloud VM detail..." />;
  }
  if (error && !detail) {
    return <ErrorState message={error} />;
  }
  if (!detail) {
    return <EmptyState label={`Cloud VM id '${String(vmId)}' was not found.`} />;
  }

  const loadingAction = Boolean(actionLoadingById[detail.id]);
  const canCreate = detail.status === "INITIALIZED" || detail.status === "FAILED";
  const powerState = String(detail.instance_power_state || "").toLowerCase();
  const isRunningByPower = powerState.includes("running");
  const canStart =
    !isRunningByPower &&
    detail.status !== "INITIALIZED" &&
    detail.status !== "PENDING" &&
    detail.status !== "RUNNING" &&
    detail.status !== "DELETING" &&
    detail.status !== "DELETED";
  const canStop =
    isRunningByPower &&
    detail.status !== "INITIALIZED" &&
    detail.status !== "PENDING" &&
    detail.status !== "RUNNING" &&
    detail.status !== "DELETING" &&
    detail.status !== "DELETED";
  const canDelete = detail.status !== "RUNNING" && detail.status !== "PENDING" && detail.status !== "DELETING";
  const latestCheck = detail.check_requests.length > 0 ? detail.check_requests[0] : null;
  const latestCheckStatus = String(latestCheck?.status || "").toUpperCase();
  const latestCheckError = String(latestCheck?.last_error || "").trim();
  const latestStart = detail.start_requests.length > 0 ? detail.start_requests[0] : null;
  const latestStartStatus = String(latestStart?.status || "").toUpperCase();
  const latestStartError = String(latestStart?.last_error || "").trim();
  const latestStop = detail.stop_requests.length > 0 ? detail.stop_requests[0] : null;
  const latestStopStatus = String(latestStop?.status || "").toUpperCase();
  const latestStopError = String(latestStop?.last_error || "").trim();
  const actionButtonSx = {
    minHeight: 30,
    height: 30,
    px: 1.4,
    whiteSpace: "nowrap",
  };

  return (
    <Stack spacing={2} sx={{ width: "100%", maxWidth: 1160, mx: "auto" }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h5">Cloud VM Detail</Typography>
        <Button component={Link} to="/cloud" variant="contained" size="small" sx={actionButtonSx}>
          Back to list
        </Button>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}

      <Paper sx={{ p: 2 }}>
        <Stack spacing={1}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.2} alignItems={{ xs: "flex-start", sm: "center" }}>
            <Typography variant="h6">
              #{detail.id} {detail.name}
            </Typography>
            <StatusBadge value={detail.status} />
            <Typography variant="body2" color="text.secondary">
              Power:
            </Typography>
            <StatusBadge value={detail.instance_power_state || "UNKNOWN"} />
          </Stack>
          <Typography variant="body2" color="text.secondary">
            Provider: {detail.provider}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Created: {formatDateTime(detail.created_at)}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Updated: {formatDateTime(detail.updated_at)}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Task ID: {detail.task_id || "-"}
          </Typography>
          {detail.inventory_vm && (
            <Typography variant="body2" color="text.secondary">
              Inventory VM: {detail.inventory_vm.name} ({detail.inventory_vm.address})
            </Typography>
          )}
          {detail.last_error && (
            <Alert severity="error">{detail.last_error}</Alert>
          )}
          <Divider sx={{ my: 1 }} />
          <Stack
            direction={{ xs: "column", md: "row" }}
            justifyContent="space-between"
            alignItems={{ xs: "stretch", md: "flex-start" }}
            spacing={2}
          >
            <Stack spacing={0.8}>
              <Typography variant="subtitle2">Instance Snapshot</Typography>
              <Typography variant="body2" color="text.secondary">
                Provider Name: {detail.instance_provider_name || detail.provider || "-"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Size: {detail.instance_size || "-"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Location: {detail.instance_location || "-"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Power State: {detail.instance_power_state || "-"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Public IP: {detail.instance_public_ip || "-"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                NIC Name: {detail.instance_nic_name || "-"}
              </Typography>
            </Stack>
            <Stack
              spacing={1}
              justifyContent="flex-end"
              alignItems={{ xs: "stretch", sm: "flex-end" }}
            >
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "stretch", sm: "center" }}>
                <Button
                  variant="contained"
                  size="small"
                  sx={actionButtonSx}
                  startIcon={<PlayArrowIcon fontSize="small" />}
                  disabled={!canCreate || loadingAction}
                  onClick={() => {
                    void startCreate(detail.id);
                  }}
                >
                  {loadingAction ? "Working..." : "Create VM"}
                </Button>
                <Button
                  variant="contained"
                  size="small"
                  sx={actionButtonSx}
                  startIcon={<RuleIcon fontSize="small" />}
                  disabled={loadingAction}
                  onClick={() => {
                    void triggerCheck(detail.id);
                  }}
                >
                  {loadingAction ? "Checking..." : "Check"}
                </Button>
                <Button
                  variant="contained"
                  size="small"
                  color="success"
                  sx={actionButtonSx}
                  startIcon={<PowerSettingsNewIcon fontSize="small" />}
                  disabled={!canStart || loadingAction}
                  onClick={() => {
                    void startVm(detail.id);
                  }}
                >
                  {loadingAction ? "Starting..." : "Start VM"}
                </Button>
                <Button
                  variant="contained"
                  size="small"
                  color="warning"
                  sx={actionButtonSx}
                  startIcon={<StopCircleIcon fontSize="small" />}
                  disabled={!canStop || loadingAction}
                  onClick={() => {
                    void stopVm(detail.id);
                  }}
                >
                  {loadingAction ? "Stopping..." : "Stop VM"}
                </Button>
                <Button
                  variant="contained"
                  size="small"
                  color="error"
                  sx={actionButtonSx}
                  disabled={!canDelete || loadingAction}
                  onClick={() => {
                    setDeleteDialogOpen(true);
                  }}
                >
                  Delete
                </Button>
              </Stack>
              {latestCheckStatus === "FAIL" && (
                <Alert severity="warning" sx={{ width: { xs: "100%", sm: 420 } }}>
                  Last check failed: {latestCheckError || "Unknown error."}
                </Alert>
              )}
              {latestStartStatus === "FAIL" && (
                <Alert severity="error" sx={{ width: { xs: "100%", sm: 420 } }}>
                  Last start failed: {latestStartError || "Unknown error."}
                </Alert>
              )}
              {latestStopStatus === "FAIL" && (
                <Alert severity="error" sx={{ width: { xs: "100%", sm: 420 } }}>
                  Last stop failed: {latestStopError || "Unknown error."}
                </Alert>
              )}
            </Stack>
          </Stack>
        </Stack>
      </Paper>

      <JsonPanel title="Raw Template" payload={detail.raw_template} />
      <JsonPanel title="Request Spec" payload={detail.request_spec} />
      <JsonPanel title="Result Data" payload={detail.result_data} />
      <JsonPanel title="Events" payload={detail.events} />
      <JsonPanel title="Check Requests" payload={detail.check_requests} />
      <JsonPanel title="Start Requests" payload={detail.start_requests} />
      <JsonPanel title="Stop Requests" payload={detail.stop_requests} />

      <Dialog
        open={deleteDialogOpen}
        onClose={() => {
          if (!loadingAction) {
            setDeleteDialogOpen(false);
          }
        }}
      >
        <DialogTitle>Delete Cloud VM</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This will deprovision cloud resources for VM #{detail.id} ({detail.name}).
            Continue?
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button
            variant="contained"
            size="small"
            sx={actionButtonSx}
            onClick={() => {
              setDeleteDialogOpen(false);
            }}
            disabled={loadingAction}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            size="small"
            sx={actionButtonSx}
            disabled={loadingAction}
            onClick={() => {
              void deleteVm(detail.id);
              setDeleteDialogOpen(false);
            }}
          >
            Confirm Delete
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
