import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import {
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
import { useEffect, useMemo } from "react";
import { Link } from "react-router-dom";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { formatDateTime } from "../../../shared/utils/date";
import { useRuntimeStore } from "../../../stores/runtime.store";
import { useUiStore } from "../../../stores/ui.store";
import type { RuntimeEnvironment } from "../types";

const ROUTE_KEY = "runtime";

function RuntimeDetailRow({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 1, py: 0.4 }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}

export function RuntimeListPage() {
  const list = useRuntimeStore((state) => state.list);
  const loadingList = useRuntimeStore((state) => state.loadingList);
  const error = useRuntimeStore((state) => state.error);
  const fetchList = useRuntimeStore((state) => state.fetchList);

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
    return list.filter((item) => item.name.toLowerCase().includes(normalized));
  }, [list, search]);

  if (loadingList && list.length === 0) {
    return <LoadingState label="Loading runtime environments..." />;
  }

  if (error && list.length === 0) {
    return <ErrorState message={error} />;
  }

  if (filtered.length === 0) {
    return (
      <Stack spacing={2}>
        <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
          <Typography variant="h5">Runtime Environments</Typography>
          <TextField
            size="small"
            value={search}
            placeholder="Filter by runtime name"
            onChange={(event) => setSearch(ROUTE_KEY, event.target.value)}
          />
        </Stack>
        <EmptyState label="No runtime environments found." />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
        <Typography variant="h5">Runtime Environments</Typography>
        <TextField
          size="small"
          value={search}
          placeholder="Filter by runtime name"
          onChange={(event) => setSearch(ROUTE_KEY, event.target.value)}
        />
      </Stack>
      {error && <ErrorState message={error} />}
      <Stack spacing={1.2}>
        {filtered.map((runtime: RuntimeEnvironment) => (
          <Accordion defaultExpanded key={runtime.name} disableGutters>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1.2}
                alignItems={{ xs: "flex-start", sm: "center" }}
                sx={{ width: "100%", pr: 1 }}
              >
                <Typography variant="subtitle1" sx={{ minWidth: 180, fontWeight: 600 }}>
                  {runtime.name}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                  {runtime.kind ?? "-"}
                </Typography>
                <Chip label={`${String((runtime.command_preset ?? []).length)} presets`} size="small" variant="outlined" />
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={1.2}>
                <RuntimeDetailRow label="Kind" value={runtime.kind ?? "-"} />
                <RuntimeDetailRow label="Command Presets" value={String((runtime.command_preset ?? []).length)} />
                <RuntimeDetailRow
                  label="Command Groups"
                  value={String(Object.keys(runtime.command_groups ?? {}).length)}
                />
                <RuntimeDetailRow label="Created" value={formatDateTime(runtime.created_at)} />
                <RuntimeDetailRow label="Updated" value={formatDateTime(runtime.updated_at)} />
                <Box>
                  <Button
                    component={Link}
                    to={`/runtime/${encodeURIComponent(runtime.name)}`}
                    variant="outlined"
                    size="small"
                    endIcon={<OpenInNewIcon fontSize="small" />}
                  >
                    Open Detail
                  </Button>
                </Box>
              </Stack>
            </AccordionDetails>
          </Accordion>
        ))}
      </Stack>
    </Stack>
  );
}
