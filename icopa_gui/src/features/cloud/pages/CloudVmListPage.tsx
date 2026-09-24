import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import PowerSettingsNewIcon from "@mui/icons-material/PowerSettingsNew";
import RuleIcon from "@mui/icons-material/Rule";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import {
  Alert,
  Box,
  Button,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { type ChangeEvent, useEffect, useMemo } from "react";
import { Link } from "react-router-dom";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { StatusBadge } from "../../../shared/components/StatusBadge";
import { formatDateTime } from "../../../shared/utils/date";
import { useCloudStore } from "../../../stores/cloud.store";
import { useUiStore } from "../../../stores/ui.store";

const ROUTE_KEY = "cloud";
const PROVIDER = "AZURE";

export function CloudVmListPage() {
  const list = useCloudStore((state) => state.list);
  const loadingList = useCloudStore((state) => state.loadingList);
  const error = useCloudStore((state) => state.error);
  const actionLoadingById = useCloudStore((state) => state.actionLoadingById);
  const fetchList = useCloudStore((state) => state.fetchList);
  const uploadTemplate = useCloudStore((state) => state.uploadTemplate);
  const startCreate = useCloudStore((state) => state.startCreate);
  const startVm = useCloudStore((state) => state.startVm);
  const deleteVm = useCloudStore((state) => state.deleteVm);
  const stopVm = useCloudStore((state) => state.stopVm);
  const triggerCheck = useCloudStore((state) => state.triggerCheck);

  const search = useUiStore((state) => state.searchByRoute[ROUTE_KEY] ?? "");
  const setSearch = useUiStore((state) => state.setSearch);

  useEffect(() => {
    void fetchList(PROVIDER);
  }, [fetchList]);

  const filtered = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    if (!normalized) {
      return list;
    }
    return list.filter((item) => {
      return (
        String(item.id).includes(normalized) ||
        item.name.toLowerCase().includes(normalized) ||
        (item.group_name || "").toLowerCase().includes(normalized) ||
        item.status.toLowerCase().includes(normalized)
      );
    });
  }, [list, search]);

  const grouped = useMemo(() => {
    const groups = new Map<string, typeof filtered>();
    for (const item of filtered) {
      const groupName = String(item.group_name || "").trim() || "default";
      const entries = groups.get(groupName) ?? [];
      entries.push(item);
      groups.set(groupName, entries);
    }
    return Array.from(groups.entries()).sort(([left], [right]) => left.localeCompare(right));
  }, [filtered]);

  const onUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    await uploadTemplate(file);
    event.target.value = "";
  };

  if (loadingList && list.length === 0) {
    return <LoadingState label="Loading cloud VMs..." />;
  }
  if (error && list.length === 0) {
    return <ErrorState message={error} />;
  }
  const actionButtonSx = {
    minHeight: 30,
    height: 30,
    px: 1.4,
    whiteSpace: "nowrap",
  };

  return (
    <Stack spacing={2} sx={{ width: "100%", maxWidth: 1160, mx: "auto" }}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
        <Typography variant="h5">Cloud VMs</Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <TextField
            size="small"
            value={search}
            placeholder="Filter by id/name/status"
            onChange={(event) => setSearch(ROUTE_KEY, event.target.value)}
            sx={{ minWidth: { xs: "100%", sm: 280 } }}
          />
          <Button component="label" variant="contained" startIcon={<CloudUploadIcon />} sx={actionButtonSx}>
            Upload Template
            <input hidden type="file" accept=".yml,.yaml" onChange={onUpload} />
          </Button>
        </Stack>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}

      {filtered.length === 0 ? (
        <EmptyState label="No cloud VMs found. Upload a template to initialize provisioning entries." />
      ) : (
        <Stack spacing={1.2}>
          {grouped.map(([groupName, groupItems]) => (
            <Box
              key={`group:${groupName}`}
              sx={{
                border: 1,
                borderColor: "divider",
                borderRadius: 2,
                p: 1.2,
                display: "grid",
                gap: 1,
              }}
            >
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "flex-start", sm: "center" }}>
                <Box
                  sx={{
                    px: 1.1,
                    py: 0.35,
                    borderRadius: 1,
                    border: 1,
                    borderColor: "divider",
                    bgcolor: "action.hover",
                  }}
                >
                  <Typography variant="subtitle1" sx={{ fontWeight: 700, lineHeight: 1.2 }}>
                    Group: {groupName}
                  </Typography>
                </Box>
                <Typography variant="body2" color="text.secondary">
                  {groupItems.length} VM{groupItems.length > 1 ? "s" : ""}
                </Typography>
              </Stack>
              <Stack spacing={1.2}>
                {groupItems.map((item) => {
                  const loadingAction = Boolean(actionLoadingById[item.id]);
                  const canCreate = item.status === "INITIALIZED" || item.status === "FAILED";
                  const powerState = String(item.instance_power_state || "").toLowerCase();
                  const isRunningByPower = powerState.includes("running");
                  const canStart =
                    !isRunningByPower &&
                    item.status !== "INITIALIZED" &&
                    item.status !== "PENDING" &&
                    item.status !== "RUNNING" &&
                    item.status !== "DELETING" &&
                    item.status !== "DELETED";
                  const canStop =
                    isRunningByPower &&
                    item.status !== "INITIALIZED" &&
                    item.status !== "PENDING" &&
                    item.status !== "RUNNING" &&
                    item.status !== "DELETING" &&
                    item.status !== "DELETED";
                  const canDelete = item.status !== "RUNNING" && item.status !== "PENDING" && item.status !== "DELETING";
                  return (
                    <Box
                      key={item.id}
                      sx={{
                        border: 1,
                        borderColor: "divider",
                        borderRadius: 2,
                        p: 1.5,
                        display: "grid",
                        gap: 1,
                      }}
                    >
                      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.2} alignItems={{ xs: "flex-start", sm: "center" }}>
                        <Typography variant="subtitle1" sx={{ minWidth: 180, fontWeight: 600 }}>
                          #{item.id} {item.name}
                        </Typography>
                        <StatusBadge value={item.status} />
                        <Typography variant="body2" color="text.secondary">
                          Power:
                        </Typography>
                        <StatusBadge value={item.instance_power_state || "UNKNOWN"} />
                        <Typography variant="body2" color="text.secondary">
                          Provider: {item.provider}
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                          Updated: {formatDateTime(item.updated_at)}
                        </Typography>
                      </Stack>
                      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
                        <Button
                          component={Link}
                          to={`/cloud/${String(item.id)}`}
                          variant="outlined"
                          size="small"
                          sx={actionButtonSx}
                          endIcon={<OpenInNewIcon fontSize="small" />}
                        >
                          Open Detail
                        </Button>
                        <Button
                          variant="contained"
                          size="small"
                          sx={actionButtonSx}
                          startIcon={<PlayArrowIcon fontSize="small" />}
                          disabled={!canCreate || loadingAction}
                          onClick={() => {
                            void startCreate(item.id);
                          }}
                        >
                          {loadingAction ? "Working..." : "Create VM"}
                        </Button>
                        <Button
                          variant="contained"
                          size="small"
                          color="success"
                          sx={actionButtonSx}
                          startIcon={<PowerSettingsNewIcon fontSize="small" />}
                          disabled={!canStart || loadingAction}
                          onClick={() => {
                            void startVm(item.id);
                          }}
                        >
                          {loadingAction ? "Starting..." : "Start VM"}
                        </Button>
                        <Button
                          variant="contained"
                          color="warning"
                          size="small"
                          sx={actionButtonSx}
                          startIcon={<StopCircleIcon fontSize="small" />}
                          disabled={!canStop || loadingAction}
                          onClick={() => {
                            void stopVm(item.id);
                          }}
                        >
                          Stop VM
                        </Button>
                        <Button
                          variant="outlined"
                          size="small"
                          sx={actionButtonSx}
                          startIcon={<RuleIcon fontSize="small" />}
                          disabled={loadingAction}
                          onClick={() => {
                            void triggerCheck(item.id);
                          }}
                        >
                          Check
                        </Button>
                        <Button
                          variant="outlined"
                          size="small"
                          color="error"
                          sx={actionButtonSx}
                          disabled={!canDelete || loadingAction}
                          onClick={() => {
                            void deleteVm(item.id);
                          }}
                        >
                          Delete
                        </Button>
                      </Stack>
                    </Box>
                  );
                })}
              </Stack>
            </Box>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
