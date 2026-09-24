import CompareArrowsIcon from "@mui/icons-material/CompareArrows";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useEffect, useMemo } from "react";
import { Link } from "react-router-dom";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { StatusBadge } from "../../../shared/components/StatusBadge";
import { formatDateTime } from "../../../shared/utils/date";
import { useExperimentStore } from "../../../stores/experiment.store";
import { useRunComparisonStore } from "../../../stores/runComparison.store";
import { useUiStore } from "../../../stores/ui.store";
import type { ProfilingGeneratedRun } from "../types";

const ROUTE_KEY = "experiment-run";

function ExperimentRunDetailRow({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 1, py: 0.4 }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}

function formatExecutionDuration(startedAt?: string | null, finishedAt?: string | null): string {
  if (!startedAt) {
    return "-";
  }
  const started = new Date(startedAt);
  if (Number.isNaN(started.getTime())) {
    return "-";
  }

  const finished = finishedAt ? new Date(finishedAt) : new Date();
  if (Number.isNaN(finished.getTime())) {
    return "-";
  }

  const diffMs = Math.max(0, finished.getTime() - started.getTime());
  const totalSeconds = Math.floor(diffMs / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

export function ExperimentRunListPage() {
  const generatedRuns = useExperimentStore((state) => state.generatedRuns);
  const loading = useExperimentStore((state) => state.loading);
  const error = useExperimentStore((state) => state.error);
  const fetchGeneratedRuns = useExperimentStore((state) => state.fetchGeneratedRuns);
  const selectedRunIds = useRunComparisonStore((state) => state.selectedRunIds);
  const toggleRun = useRunComparisonStore((state) => state.toggleRun);

  const search = useUiStore((state) => state.searchByRoute[ROUTE_KEY] ?? "");
  const setSearch = useUiStore((state) => state.setSearch);

  useEffect(() => {
    void fetchGeneratedRuns();
  }, [fetchGeneratedRuns]);

  const filtered = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    if (!normalized) {
      return generatedRuns;
    }
    return generatedRuns.filter((item) => {
      const idMatch = String(item.id).includes(normalized);
      const genMatch = String(item.gen_version ?? "").includes(normalized);
      const expName = (item.experiment_name ?? item.generated_plan_name ?? "").toLowerCase();
      const expMatch = expName.includes(normalized);
      return idMatch || genMatch || expMatch;
    });
  }, [generatedRuns, search]);

  const groupedRuns = useMemo(() => {
    const map = new Map<string, ProfilingGeneratedRun[]>();
    filtered.forEach((run) => {
      const groupName = (run.experiment_name ?? run.generated_plan_name ?? "Unknown Experiment").trim() || "Unknown Experiment";
      const groupRows = map.get(groupName) ?? [];
      groupRows.push(run);
      map.set(groupName, groupRows);
    });
    return [...map.entries()]
      .map(([experimentName, runs]) => ({
        experimentName,
        runs: runs.sort((left, right) => right.id - left.id),
      }))
      .sort((left, right) => left.experimentName.localeCompare(right.experimentName));
  }, [filtered]);

  if (loading && generatedRuns.length === 0) {
    return <LoadingState label="Loading experiment runs..." />;
  }

  if (error && generatedRuns.length === 0) {
    return <ErrorState message={error} />;
  }

  if (filtered.length === 0) {
    return (
      <Stack spacing={2}>
        <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
          <Typography variant="h5">Experiment Runs</Typography>
          <Stack direction="row" spacing={1}>
            <TextField
              size="small"
              value={search}
              placeholder="Filter by id/gen/experiment"
              onChange={(event) => setSearch(ROUTE_KEY, event.target.value)}
            />
            <Button component={Link} to="/experiment/comparison" variant="outlined" size="small">
              Comparison ({selectedRunIds.length})
            </Button>
          </Stack>
        </Stack>
        <EmptyState label="No experiment runs found." />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
        <Typography variant="h5">Experiment Runs</Typography>
        <Stack direction="row" spacing={1}>
          <TextField
            size="small"
            value={search}
            placeholder="Filter by id/gen/experiment"
            onChange={(event) => setSearch(ROUTE_KEY, event.target.value)}
          />
          <Button component={Link} to="/experiment/comparison" variant="outlined" size="small">
            Comparison ({selectedRunIds.length})
          </Button>
        </Stack>
      </Stack>
      {error && <ErrorState message={error} />}
      <Stack spacing={1.2}>
        {groupedRuns.map((group) => (
          <Accordion defaultExpanded key={group.experimentName} disableGutters>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1.2}
                alignItems={{ xs: "flex-start", sm: "center" }}
                sx={{ width: "100%", pr: 1 }}
              >
                <Typography variant="subtitle1" sx={{ minWidth: 180, fontWeight: 600 }}>
                  {group.experimentName}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                  {group.runs.length} runs
                </Typography>
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={1}>
                {group.runs.map((generatedRun) => (
                  <Paper key={generatedRun.id} variant="outlined" sx={{ p: 1.1 }}>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      spacing={1}
                      justifyContent="space-between"
                      alignItems={{ xs: "flex-start", sm: "center" }}
                    >
                      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.2} alignItems={{ sm: "center" }}>
                        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                          Run #{generatedRun.id} {generatedRun.plan_run_id ? `(${generatedRun.plan_run_id})` : ""}
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                          Gen v{generatedRun.gen_version ?? "-"}
                        </Typography>
                        <StatusBadge value={generatedRun.status} />
                      </Stack>
                      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
                        <Button
                          variant={selectedRunIds.includes(generatedRun.id) ? "contained" : "outlined"}
                          size="small"
                          startIcon={<CompareArrowsIcon fontSize="small" />}
                          onClick={() => toggleRun(generatedRun.id)}
                        >
                          {selectedRunIds.includes(generatedRun.id) ? "In Comparison" : "Compare"}
                        </Button>
                        <Button
                          component={Link}
                          to={`/experiment/runs/${generatedRun.id}`}
                          variant="outlined"
                          size="small"
                          endIcon={<OpenInNewIcon fontSize="small" />}
                        >
                          Open Run
                        </Button>
                      </Box>
                    </Stack>
                    <Stack spacing={0.4} sx={{ mt: 0.8 }}>
                      <ExperimentRunDetailRow label="Started" value={formatDateTime(generatedRun.started_at)} />
                      <ExperimentRunDetailRow
                        label="Execution Duration"
                        value={formatExecutionDuration(generatedRun.started_at, generatedRun.finished_at)}
                      />
                    </Stack>
                  </Paper>
                ))}
              </Stack>
            </AccordionDetails>
          </Accordion>
        ))}
      </Stack>
    </Stack>
  );
}
