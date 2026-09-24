import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { StatusBadge } from "../../../shared/components/StatusBadge";
import { formatDateTime } from "../../../shared/utils/date";
import { useScenarioStore } from "../../../stores/scenario.store";
import { useUiStore } from "../../../stores/ui.store";
import type { Scenario } from "../types";

const ROUTE_KEY = "scenario";

function ScenarioDetailRow({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 1, py: 0.4 }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}

export function ScenarioListPage() {
  const list = useScenarioStore((state) => state.list);
  const loadingList = useScenarioStore((state) => state.loadingList);
  const error = useScenarioStore((state) => state.error);
  const fetchList = useScenarioStore((state) => state.fetchList);

  const search = useUiStore((state) => state.searchByRoute[ROUTE_KEY] ?? "");
  const setSearch = useUiStore((state) => state.setSearch);
  const [sortOrder, setSortOrder] = useState<"newest" | "oldest">("newest");

  useEffect(() => {
    void fetchList();
  }, [fetchList]);

  const filtered = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    const base = !normalized ? list : list.filter((item) => item.name.toLowerCase().includes(normalized));
    const withTime = [...base];
    withTime.sort((left, right) => {
      const leftTime = Date.parse(left.updated_at || left.created_at || "");
      const rightTime = Date.parse(right.updated_at || right.created_at || "");
      const safeLeft = Number.isFinite(leftTime) ? leftTime : 0;
      const safeRight = Number.isFinite(rightTime) ? rightTime : 0;
      return sortOrder === "newest" ? safeRight - safeLeft : safeLeft - safeRight;
    });
    return withTime;
  }, [list, search, sortOrder]);

  if (loadingList && list.length === 0) {
    return <LoadingState label="Loading scenarios..." />;
  }

  if (error && list.length === 0) {
    return <ErrorState message={error} />;
  }

  if (filtered.length === 0) {
    return (
      <Stack spacing={2}>
        <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
          <Typography variant="h5">Scenarios</Typography>
          <Stack direction="row" spacing={1}>
            <TextField
              size="small"
              value={search}
              placeholder="Filter by scenario name"
              onChange={(event) => setSearch(ROUTE_KEY, event.target.value)}
            />
            <Button
              size="small"
              variant="outlined"
              onClick={() => setSortOrder((current) => (current === "newest" ? "oldest" : "newest"))}
            >
              {sortOrder === "newest" ? "Newest" : "Oldest"}
            </Button>
          </Stack>
        </Stack>
        <EmptyState label="No scenarios found." />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
        <Typography variant="h5">Scenarios</Typography>
        <Stack direction="row" spacing={1}>
          <TextField
            size="small"
            value={search}
            placeholder="Filter by scenario name"
            onChange={(event) => setSearch(ROUTE_KEY, event.target.value)}
          />
          <Button
            size="small"
            variant="outlined"
            onClick={() => setSortOrder((current) => (current === "newest" ? "oldest" : "newest"))}
          >
            {sortOrder === "newest" ? "Newest" : "Oldest"}
          </Button>
        </Stack>
      </Stack>
      {error && <ErrorState message={error} />}
      <Stack spacing={1.2}>
        {filtered.map((scenario: Scenario) => (
          <Accordion defaultExpanded key={scenario.name} disableGutters>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1.2}
                alignItems={{ xs: "flex-start", sm: "center" }}
                sx={{ width: "100%", pr: 1 }}
              >
                <Typography variant="subtitle1" sx={{ minWidth: 180, fontWeight: 600 }}>
                  {scenario.name}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                  {scenario.kind ?? "Scenario"}
                </Typography>
                <StatusBadge value={scenario.validation_status} />
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={1.2}>
                <ScenarioDetailRow label="Kind" value={scenario.kind ?? "Scenario"} />
                <ScenarioDetailRow label="Validation" value={(scenario.validation_status ?? "UNKNOWN").toUpperCase()} />
                <ScenarioDetailRow
                  label="Action Check"
                  value={(scenario.check_status_actions ?? "UNKNOWN").toUpperCase()}
                />
                <ScenarioDetailRow label="Node Check" value={(scenario.check_status_node ?? "UNKNOWN").toUpperCase()} />
                <ScenarioDetailRow label="Graph Check" value={(scenario.check_status_graph ?? "UNKNOWN").toUpperCase()} />
                <ScenarioDetailRow label="Updated" value={formatDateTime(scenario.updated_at)} />
                <Box>
                  <Button
                    component={Link}
                    to={`/scenario/${encodeURIComponent(scenario.name)}`}
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
