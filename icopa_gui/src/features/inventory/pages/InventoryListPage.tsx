import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import MonitorHeartIcon from "@mui/icons-material/MonitorHeart";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import {
  Alert,
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Chip,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { type ReactNode, useEffect, useMemo } from "react";
import { Link } from "react-router-dom";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { formatDateTimeWithRelative } from "../../../shared/utils/date";
import { useInventoryStore } from "../../../stores/inventory.store";
import { useUiStore } from "../../../stores/ui.store";
import { getSystemInfoSummary } from "../display";
import type { InventoryVm } from "../types";

const ROUTE_KEY = "inventory";

function getSiteInfo(vm: InventoryVm): string {
  const metadata = vm.metadata ?? {};
  const networking = vm.networking ?? {};
  const candidates = [
    metadata.site,
    metadata.site_name,
    metadata.siteName,
    metadata.location,
    metadata.region,
    metadata.zone,
    metadata.datacenter,
    metadata.data_center,
    networking.site,
    networking.location,
    networking.region,
    networking.zone,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }
  return "-";
}

function VmDetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "minmax(0, 1fr)", sm: "160px minmax(0, 1fr)" }, gap: 1, py: 0.4 }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Box sx={{ minWidth: 0 }}>{typeof value === "string" ? <Typography variant="body2">{value}</Typography> : value}</Box>
    </Box>
  );
}

function VmDetailTwoColumnValue({
  leftLabel,
  leftValue,
  rightLabel,
  rightValue,
}: {
  leftLabel?: string;
  leftValue: string;
  rightLabel?: string;
  rightValue: string;
}) {
  const valueCellSx = { display: "grid", gridTemplateColumns: { xs: "100px minmax(0, 1fr)", md: "130px minmax(0, 1fr)" }, gap: 0.75, alignItems: "start" };
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "minmax(0, 1fr) minmax(0, 1fr)" }, gap: 1 }}>
      {leftLabel ? (
        <Box sx={valueCellSx}>
          <Typography variant="body2" color="text.secondary">
            {leftLabel}:
          </Typography>
          <Typography variant="body2">{leftValue}</Typography>
        </Box>
      ) : (
        <Typography variant="body2">{leftValue}</Typography>
      )}
      {rightLabel ? (
        <Box sx={valueCellSx}>
          <Typography variant="body2" color="text.secondary">
            {rightLabel}:
          </Typography>
          <Typography variant="body2">{rightValue}</Typography>
        </Box>
      ) : (
        <Typography variant="body2">{rightValue}</Typography>
      )}
    </Box>
  );
}

export function InventoryListPage() {
  const list = useInventoryStore((state) => state.list);
  const loadingList = useInventoryStore((state) => state.loadingList);
  const error = useInventoryStore((state) => state.error);
  const fetchList = useInventoryStore((state) => state.fetchList);
  const checkVm = useInventoryStore((state) => state.checkVm);
  const checkingByName = useInventoryStore((state) => state.checkingByName);
  const checkResultByName = useInventoryStore((state) => state.checkResultByName);
  const checkErrorByName = useInventoryStore((state) => state.checkErrorByName);

  const search = useUiStore((state) => state.searchByRoute[ROUTE_KEY] ?? "");
  const setSearch = useUiStore((state) => state.setSearch);

  useEffect(() => {
    void fetchList();
  }, [fetchList]);

  const filtered = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    if (!normalized) {
      return list;
    }
    return list.filter((item) => {
      return (
        item.name.toLowerCase().includes(normalized) ||
        (item.address ?? "").toLowerCase().includes(normalized) ||
        (item.user_name ?? "").toLowerCase().includes(normalized)
      );
    });
  }, [list, search]);

  const grouped = useMemo(() => {
    const groups = new Map<string, InventoryVm[]>();
    for (const vm of filtered) {
      const groupName = (vm.group_name ?? "").trim() || "default";
      const items = groups.get(groupName) ?? [];
      items.push(vm);
      groups.set(groupName, items);
    }
    return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  if (loadingList && list.length === 0) {
    return <LoadingState label="Loading inventory..." />;
  }
  if (error && list.length === 0) {
    return <ErrorState message={error} />;
  }

  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
        <Typography variant="h5">Inventory</Typography>
        <TextField
          size="small"
          value={search}
          placeholder="Filter by name/address/user"
          onChange={(event) => setSearch(ROUTE_KEY, event.target.value)}
          sx={{ minWidth: { xs: "100%", sm: 320 } }}
        />
      </Stack>

      {error && <ErrorState message={error} />}

      {filtered.length === 0 ? (
        <EmptyState label="No VM inventory found." />
      ) : (
        <Stack spacing={1.2}>
          {grouped.map(([groupName, groupVms]) => (
            <Accordion key={groupName} defaultExpanded disableGutters>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Stack direction="row" spacing={1.2} alignItems="center" sx={{ width: "100%", pr: 1 }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                    Group: {groupName}
                  </Typography>
                  <Chip size="small" variant="outlined" label={`${groupVms.length} VM${groupVms.length > 1 ? "s" : ""}`} />
                </Stack>
              </AccordionSummary>
              <AccordionDetails>
                <Stack spacing={1}>
                  {groupVms.map((vm: InventoryVm) => (
                    <Accordion defaultExpanded key={`${groupName}:${vm.name}`} disableGutters>
                      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                        <Stack
                          direction={{ xs: "column", sm: "row" }}
                          spacing={1.2}
                          alignItems={{ xs: "flex-start", sm: "center" }}
                          sx={{ width: "100%", pr: 1 }}
                        >
                          <Typography variant="subtitle1" sx={{ minWidth: 0, fontWeight: 600, overflowWrap: "anywhere" }}>
                            {vm.name}
                          </Typography>
                          <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                            {getSiteInfo(vm)}
                          </Typography>
                          <Chip label={vm.status ?? "UNKNOWN"} size="small" variant="outlined" />
                        </Stack>
                      </AccordionSummary>
                      <AccordionDetails>
                        <Stack spacing={1.2}>
                          <Stack direction="row" gap={1} flexWrap="wrap">
                            <Button
                              component={Link}
                              to={`/inventory/${encodeURIComponent(vm.name)}`}
                              variant="outlined" size="small"
                              endIcon={<OpenInNewIcon fontSize="small" />}
                            >
                              Open Detail
                            </Button>
                            <Button
                              variant="contained" size="small" color="secondary"
                              startIcon={<MonitorHeartIcon fontSize="small" />}
                              disabled={Boolean(checkingByName[vm.name])}
                              onClick={() => { void checkVm(vm.name); }}
                            >
                              {checkingByName[vm.name] ? "Checking..." : "Check VM"}
                            </Button>
                          </Stack>
                          {(() => {
                            const summary = getSystemInfoSummary(vm.system_info);
                            const cpu = summary.find((row) => row.label === "CPU Cores")?.value ?? "-";
                            const ram = summary.find((row) => row.label === "RAM")?.value ?? "-";
                            const gpu = summary.find((row) => row.label === "GPU")?.value ?? "-";
                            const address = vm.address ?? "-";
                            const port = String(vm.port ?? "-");
                            const sshUser = vm.user_name ?? "-";
                            const lastConnection = formatDateTimeWithRelative(vm.last_connection_time);

                            return (
                              <>
                                <VmDetailRow
                                  label="CPU/RAM"
                                  value={
                                    <VmDetailTwoColumnValue
                                      leftValue={`${cpu} / ${ram}`}
                                      rightLabel="GPU"
                                      rightValue={gpu}
                                    />
                                  }
                                />
                                <VmDetailRow
                                  label="Network/SSH-Port"
                                  value={
                                    <VmDetailTwoColumnValue
                                      leftValue={`${address}:${port}`}
                                      rightLabel="SSH User"
                                      rightValue={sshUser}
                                    />
                                  }
                                />
                                <VmDetailRow
                                  label="Last Connection"
                                  value={lastConnection}
                                />
                              </>
                            );
                          })()}
                          {checkErrorByName[vm.name] && <Alert severity="error">{checkErrorByName[vm.name]}</Alert>}
                          <VmDetailRow
                            label="Managed Containers"
                            value={(vm.managed_containers ?? []).length > 0 ? (vm.managed_containers ?? []).join(", ") : "-"}
                          />
                          {checkResultByName[vm.name] && (
                            <VmDetailRow
                              label="Last Check"
                              value={
                                checkResultByName[vm.name]?.ok
                                  ? `OK (code=${String(checkResultByName[vm.name]?.returncode ?? 0)})`
                                  : `FAILED (code=${String(checkResultByName[vm.name]?.returncode ?? "-")})`
                              }
                            />
                          )}
                        </Stack>
                      </AccordionDetails>
                    </Accordion>
                  ))}
                </Stack>
              </AccordionDetails>
            </Accordion>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
