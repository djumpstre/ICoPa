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
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { StatusBadge } from "../../../shared/components/StatusBadge";
import { formatDateTime } from "../../../shared/utils/date";
import { useExperimentStore } from "../../../stores/experiment.store";
import { useUiStore } from "../../../stores/ui.store";
import type { ProfilingExperiment } from "../types";

const ROUTE_KEY = "experiment";

function ExperimentDetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 1, py: 0.4 }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}

export function ExperimentListPage() {
  const experiments = useExperimentStore((state) => state.experiments);
  const loading = useExperimentStore((state) => state.loading);
  const error = useExperimentStore((state) => state.error);
  const fetchExperiments = useExperimentStore((state) => state.fetchExperiments);

  const search = useUiStore((state) => state.searchByRoute[ROUTE_KEY] ?? "");
  const setSearch = useUiStore((state) => state.setSearch);
  const [sortByCreatedDesc, setSortByCreatedDesc] = useState(true);

  useEffect(() => {
    void fetchExperiments();
  }, [fetchExperiments]);

  const filtered = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    if (!normalized) {
      return experiments;
    }
    return experiments.filter((item) => item.name.toLowerCase().includes(normalized));
  }, [experiments, search]);

  const sortedFiltered = useMemo(() => {
    const rows = [...filtered];
    rows.sort((left, right) => {
      const leftTime = new Date(left.created_at ?? "").getTime();
      const rightTime = new Date(right.created_at ?? "").getTime();
      const leftValid = Number.isFinite(leftTime);
      const rightValid = Number.isFinite(rightTime);
      if (leftValid && rightValid) {
        return sortByCreatedDesc ? rightTime - leftTime : leftTime - rightTime;
      }
      if (leftValid !== rightValid) {
        return leftValid ? -1 : 1;
      }
      return left.name.localeCompare(right.name);
    });
    return rows;
  }, [filtered, sortByCreatedDesc]);

  if (loading && experiments.length === 0) {
    return <LoadingState label="Loading experiments..." />;
  }

  if (error && experiments.length === 0) {
    return <ErrorState message={error} />;
  }

  if (filtered.length === 0) {
    return (
      <Stack spacing={2}>
        <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
          <Typography variant="h5">Experiments</Typography>
          <Stack direction="row" spacing={1}>
            <TextField
              size="small"
              value={search}
              placeholder="Filter by experiment name"
              onChange={(event) => setSearch(ROUTE_KEY, event.target.value)}
            />
            <Button variant="outlined" size="small" onClick={() => setSortByCreatedDesc((prev) => !prev)}>
              Sort: {sortByCreatedDesc ? "Newest" : "Oldest"}
            </Button>
          </Stack>
        </Stack>
        <EmptyState label="No experiments found." />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
        <Typography variant="h5">Experiments</Typography>
        <Stack direction="row" spacing={1}>
          <TextField
            size="small"
            value={search}
            placeholder="Filter by experiment name"
            onChange={(event) => setSearch(ROUTE_KEY, event.target.value)}
          />
          <Button variant="outlined" size="small" onClick={() => setSortByCreatedDesc((prev) => !prev)}>
            Sort: {sortByCreatedDesc ? "Newest" : "Oldest"}
          </Button>
        </Stack>
      </Stack>
      {error && <ErrorState message={error} />}
      <Stack spacing={1.2}>
        {sortedFiltered.map((experiment: ProfilingExperiment) => (
          <Accordion defaultExpanded key={experiment.name} disableGutters>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1.2}
                alignItems={{ xs: "flex-start", sm: "center" }}
                sx={{ width: "100%", pr: 1 }}
              >
                <Typography variant="subtitle1" sx={{ minWidth: 180, fontWeight: 600 }}>
                  {experiment.name}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                  {experiment.scenario_name ?? "-"}
                </Typography>
                <Stack direction="row" spacing={0.8} alignItems="center">
                  <StatusBadge value={experiment.status} />
                  <Button
                    component={Link}
                    to={`/experiment/${encodeURIComponent(experiment.name)}`}
                    variant="outlined"
                    size="small"
                    endIcon={<OpenInNewIcon fontSize="small" />}
                    onClick={(event) => event.stopPropagation()}
                    onFocus={(event) => event.stopPropagation()}
                  >
                    Open Detail
                  </Button>
                </Stack>
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={1.2}>
                <ExperimentDetailRow
                  label="Scenario"
                  value={
                    experiment.scenario_name ? (
                      <Button
                        component={Link}
                        to={`/scenario/${encodeURIComponent(experiment.scenario_name)}`}
                        variant="outlined"
                        size="small"
                      >
                        {experiment.scenario_name}
                      </Button>
                    ) : (
                      "-"
                    )
                  }
                />
                <ExperimentDetailRow label="Status" value={(experiment.status ?? "UNKNOWN").toUpperCase()} />
                <ExperimentDetailRow label="Start Count" value={String(experiment.start_count ?? 0)} />
                <ExperimentDetailRow label="Last Started" value={formatDateTime(experiment.last_started_at)} />
                <ExperimentDetailRow
                  label="Generated Plan"
                  value={experiment.has_generated_plan ? "Yes" : "No"}
                />
                <Box>
                  <Button
                    component={Link}
                    to={`/experiment/${encodeURIComponent(experiment.name)}`}
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
