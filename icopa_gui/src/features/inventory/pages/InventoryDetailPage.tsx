import MonitorHeartIcon from "@mui/icons-material/MonitorHeart";
import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Paper, Stack, Typography } from "@mui/material";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { useInventoryStore } from "../../../stores/inventory.store";
import { VmSummaryCard } from "../components/VmSummaryCard";
import { getObjectRows, getSystemInfoRows } from "../display";
import { InventoryContainerStatus } from "../types";

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: 1, py: 0.4 }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item));
}

export function InventoryDetailPage() {
  const params = useParams<{ vmName: string }>();
  const vmName = params.vmName ?? "";
  const navigate = useNavigate();

  const detail = useInventoryStore((state) => state.detailByName[vmName]);
  const loadingDetail = useInventoryStore((state) => state.loadingDetail);
  const error = useInventoryStore((state) => state.error);
  const fetchDetail = useInventoryStore((state) => state.fetchDetail);
  const checkVm = useInventoryStore((state) => state.checkVm);
  const stopContainer = useInventoryStore((state) => state.stopContainer);
  const deleteVm = useInventoryStore((state) => state.deleteVm);
  const deletingByName = useInventoryStore((state) => state.deletingByName);
  const deleteErrorByName = useInventoryStore((state) => state.deleteErrorByName);
  const checkingByName = useInventoryStore((state) => state.checkingByName);
  const checkResultByName = useInventoryStore((state) => state.checkResultByName);
  const checkErrorByName = useInventoryStore((state) => state.checkErrorByName);
  const stoppingContainerByKey = useInventoryStore((state) => state.stoppingContainerByKey);
  const stopContainerErrorByKey = useInventoryStore((state) => state.stopContainerErrorByKey);
  const stopContainerResultByKey = useInventoryStore((state) => state.stopContainerResultByKey);
  const [deleteDialogStep, setDeleteDialogStep] = useState<0 | 1 | 2>(0);

  useEffect(() => {
    if (vmName) {
      void fetchDetail(vmName);
    }
  }, [fetchDetail, vmName]);

  const checking = Boolean(checkingByName[vmName]);
  const deleting = Boolean(deletingByName[vmName]);
  const deleteError = deleteErrorByName[vmName];
  const checkResult = checkResultByName[vmName];
  const checkError = checkErrorByName[vmName];
  const systemInfoRows = getSystemInfoRows(detail?.system_info);
  const metadataRows = getObjectRows(detail?.metadata);
  const networking = asRecord(detail?.networking) ?? {};
  const networkingPublic = networking.public;
  const openPorts = asRecordArray(networking.openPorts ?? networking.open_ports);
  const containerStatus = (
    (Array.isArray(checkResult?.managed_container_status) ? checkResult?.managed_container_status : undefined)
    ?? (Array.isArray((detail?.metadata as Record<string, unknown> | undefined)?.managed_container_status)
      ? ((detail?.metadata as Record<string, unknown>).managed_container_status as InventoryContainerStatus[])
      : [])
  ).filter((item): item is InventoryContainerStatus => Boolean(item && typeof item === "object"));
  const statusByName = new Map<string, InventoryContainerStatus>();
  for (const item of containerStatus) {
    const name = String(item.name ?? "").trim();
    if (!name) continue;
    statusByName.set(name, item);
  }
  const containers = Array.from(
    new Set([
      ...(detail?.managed_containers ?? []),
      ...(checkResult?.managed_containers ?? []),
      ...Array.from(statusByName.keys()),
    ]),
  ).filter((item) => String(item ?? "").trim().length > 0);

  if (!vmName) {
    return <EmptyState label="Missing VM name." />;
  }

  if (loadingDetail && !detail) {
    return <LoadingState label="Loading VM detail..." />;
  }
  if (error && !detail) {
    return <ErrorState message={error} />;
  }
  if (!detail) {
    return <EmptyState label={`VM '${vmName}' was not found.`} />;
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h5">Inventory Detail</Typography>
        <Button component={Link} to="/inventory" variant="outlined" size="small">
          Back to list
        </Button>
      </Stack>
      <Box sx={{ display: "flex", justifyContent: "flex-start" }}>
        <Button
          variant="contained"
          size="small"
          color="secondary"
          startIcon={<MonitorHeartIcon fontSize="small" />}
          disabled={checking}
          onClick={() => {
            void checkVm(vmName);
          }}
        >
          {checking ? "Checking..." : "Check VM"}
        </Button>
      </Box>
      {error && <ErrorState message={error} />}
      {deleteError && <Alert severity="error">{deleteError}</Alert>}
      {checkError && <Alert severity="error">{checkError}</Alert>}
      {checkResult && (
        <Alert severity={checkResult.ok ? "success" : "warning"}>
          {checkResult.ok
            ? `Last check succeeded (code=${String(checkResult.returncode ?? 0)}).`
            : `Last check failed (code=${String(checkResult.returncode ?? "-")}).`}
        </Alert>
      )}
      <VmSummaryCard vm={detail} />
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          System Info
        </Typography>
        {Object.keys(detail.system_info ?? {}).length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No system info available yet. Run Check VM to collect it.
          </Typography>
        ) : (
          <Stack spacing={0.25}>
            {systemInfoRows.map((row) => (
              <DetailRow key={`system-${row.label}`} label={row.label} value={row.value} />
            ))}
          </Stack>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Containers
        </Typography>
        {containers.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No managed containers available yet. Run Check VM to collect them.
          </Typography>
        ) : (
          <Stack component="ul" sx={{ m: 0, pl: 2 }} spacing={0.5}>
            {containers.map((container) => {
              const actionKey = `${vmName}::${container}`;
              const stopping = Boolean(stoppingContainerByKey[actionKey]);
              const stopError = stopContainerErrorByKey[actionKey];
              const stopResult = stopContainerResultByKey[actionKey];
              return (
                <Box key={container} component="li" sx={{ py: 0.2 }}>
                  <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "flex-start", sm: "center" }}>
                    <Typography variant="body2">{container}</Typography>
                    {statusByName.get(container) && (
                      <Typography variant="caption" color="text.secondary">
                        {[
                          statusByName.get(container)?.state ? `state=${String(statusByName.get(container)?.state)}` : "",
                          statusByName.get(container)?.status ? `status=${String(statusByName.get(container)?.status)}` : "",
                          statusByName.get(container)?.image ? `image=${String(statusByName.get(container)?.image)}` : "",
                          statusByName.get(container)?.id ? `id=${String(statusByName.get(container)?.id)}` : "",
                          statusByName.get(container)?.ports ? `ports=${String(statusByName.get(container)?.ports)}` : "",
                          statusByName.get(container)?.running_for ? `running=${String(statusByName.get(container)?.running_for)}` : "",
                        ]
                          .filter(Boolean)
                          .join(" | ")}
                      </Typography>
                    )}
                    <Button
                      size="small"
                      variant="outlined"
                      color="warning"
                      disabled={stopping}
                      onClick={() => {
                        void stopContainer({
                          vmName,
                          vmId: typeof detail.id === "number" ? detail.id : undefined,
                          containerName: container,
                        });
                      }}
                    >
                      {stopping ? "Stopping..." : "Stop & Remove"}
                    </Button>
                    {stopError && (
                      <Typography variant="caption" color="error">
                        {stopError}
                      </Typography>
                    )}
                    {!stopError && stopResult?.message && (
                      <Typography variant="caption" color={stopResult.ok ? "success.main" : "warning.main"}>
                        {stopResult.message}
                      </Typography>
                    )}
                  </Stack>
                </Box>
              );
            })}
          </Stack>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Networking
        </Typography>
        {Object.keys(networking).length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No networking config provided.
          </Typography>
        ) : (
          <Stack spacing={0.6}>
            <DetailRow
              label="Public"
              value={
                typeof networkingPublic === "boolean"
                  ? (networkingPublic ? "true" : "false")
                  : "-"
              }
            />
            <Typography variant="body2" color="text.secondary">
              Open Ports
            </Typography>
            {openPorts.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No open ports configured.
              </Typography>
            ) : (
              <Stack component="ul" sx={{ m: 0, pl: 2 }} spacing={0.4}>
                {openPorts.map((entry, index) => {
                  const proto = String(entry.proto ?? entry.protocol ?? "-");
                  const port = String(entry.port ?? "-");
                  const purpose = String(entry.purpose ?? "-");
                  return (
                    <Box key={`open-port-${index}`} component="li">
                      <Typography variant="body2">
                        {`${proto.toUpperCase()}:${port} (${purpose})`}
                      </Typography>
                    </Box>
                  );
                })}
              </Stack>
            )}
          </Stack>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Metadata
        </Typography>
        {metadataRows.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No metadata available.
          </Typography>
        ) : (
          <Stack spacing={0.25}>
            {metadataRows.map((row) => (
              <DetailRow key={`metadata-${row.label}`} label={row.label} value={row.value} />
            ))}
          </Stack>
        )}
      </Paper>
      <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
        <Button
          variant="contained"
          color="error"
          disabled={deleting}
          onClick={() => {
            setDeleteDialogStep(1);
          }}
        >
          {deleting ? "Deleting..." : "Delete VM"}
        </Button>
      </Box>
      <Dialog
        open={deleteDialogStep > 0}
        onClose={() => {
          if (!deleting) {
            setDeleteDialogStep(0);
          }
        }}
      >
        <DialogTitle>{deleteDialogStep === 1 ? "Confirm Delete VM" : "Final Confirmation"}</DialogTitle>
        <DialogContent>
          {deleteDialogStep === 1 ? (
            <Typography variant="body2">
              You are about to delete VM <strong>{vmName}</strong>. This action cannot be undone.
            </Typography>
          ) : (
            <Typography variant="body2">
              Please confirm again to permanently delete VM <strong>{vmName}</strong>.
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => {
              setDeleteDialogStep(0);
            }}
            disabled={deleting}
          >
            Cancel
          </Button>
          {deleteDialogStep === 1 ? (
            <Button
              color="warning"
              onClick={() => {
                setDeleteDialogStep(2);
              }}
              disabled={deleting}
            >
              Continue
            </Button>
          ) : (
            <Button
              color="error"
              variant="contained"
              disabled={deleting}
              onClick={async () => {
                await deleteVm(vmName);
                if (!useInventoryStore.getState().deleteErrorByName[vmName]) {
                  setDeleteDialogStep(0);
                  navigate("/inventory");
                }
              }}
            >
              {deleting ? "Deleting..." : "Delete VM"}
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
