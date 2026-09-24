import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { useEffect, useMemo, useState } from "react";
import { Link as RouterLink, useParams } from "react-router-dom";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { useExperimentStore } from "../../../stores/experiment.store";
import { RunMeasurementStatsTable, type RunMeasurementStatsTableRow } from "../components/RunMeasurementStatsTable";
import { RttCurveOverlay, type RttCurveLine } from "../components/RttCurveOverlay";
import { loadCurvesFromExperimentRun } from "../components/rrtCurveData";
import { buildRunMeasurementStatsRow } from "../components/runMeasurementStats";
import type { ProfilingGeneratedRun } from "../types";

interface PlotArtifactPathRow {
  run_db_id: number;
  run_id: string;
  experiment_json_path: string;
  latency_csv_path: string;
}

function getString(value: unknown): string {
  if (typeof value === "string") {
    return value.trim();
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return "";
}

function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
}

function normalizeRelativePath(pathText: unknown): string {
  return getString(pathText).replace(/^\/+/, "").replace(/\/+$/, "");
}

function joinRelativePath(basePath: unknown, fileName: string): string {
  const normalizedBase = normalizeRelativePath(basePath);
  if (!normalizedBase) {
    return "";
  }
  return `${normalizedBase}/vm_home/${fileName}`;
}

function buildRunArtifactPathRows(detail: ProfilingGeneratedRun): PlotArtifactPathRow[] {
  const collectedMetricsBase = normalizeRelativePath(detail.collected_metrics_path ?? "");
  const metricsBaseByRunId = new Map<string, string>();
  asRecordArray(detail.results).forEach((item) => {
    const runId = getString(item.run_id);
    if (!runId) {
      return;
    }
    const metricsBackendDir = normalizeRelativePath(item.metrics_backend_dir);
    if (metricsBackendDir) {
      metricsBaseByRunId.set(runId, metricsBackendDir);
      return;
    }
    if (collectedMetricsBase) {
      metricsBaseByRunId.set(runId, `${collectedMetricsBase}/${runId}`);
    }
  });

  const runIds = new Set<string>(metricsBaseByRunId.keys());
  return [...runIds]
    .sort((left, right) => left.localeCompare(right))
    .map((runId) => {
      const basePath = metricsBaseByRunId.get(runId) ?? (collectedMetricsBase ? `${collectedMetricsBase}/${runId}` : "");
      return {
        run_db_id: detail.id,
        run_id: runId,
        experiment_json_path: joinRelativePath(basePath, "rrt_summary.json"),
        latency_csv_path: joinRelativePath(basePath, "rrt_all.csv"),
      };
    });
}

export function StoredComparisionDetailPage() {
  const params = useParams<{ comparisionId: string }>();
  const comparisionId = Number(params.comparisionId);

  const comparisions = useExperimentStore((state) => state.comparisions);
  const loading = useExperimentStore((state) => state.loading);
  const error = useExperimentStore((state) => state.error);
  const fetchComparisions = useExperimentStore((state) => state.fetchComparisions);
  const generatedRunDetailById = useExperimentStore((state) => state.generatedRunDetailById);
  const fetchGeneratedRunDetail = useExperimentStore((state) => state.fetchGeneratedRunDetail);

  const [loadingDetails, setLoadingDetails] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [loadingCurves, setLoadingCurves] = useState(false);
  const [curveError, setCurveError] = useState("");
  const [curves, setCurves] = useState<RttCurveLine[]>([]);
  const [pathsDialogOpen, setPathsDialogOpen] = useState(false);
  const [pathsCopyResult, setPathsCopyResult] = useState<{ severity: "success" | "error"; message: string } | null>(null);

  useEffect(() => {
    void fetchComparisions();
  }, [fetchComparisions]);

  const selectedComparision = useMemo(
    () => comparisions.find((item) => item.id === comparisionId),
    [comparisionId, comparisions],
  );

  const activeRunIds = useMemo(
    () =>
      (selectedComparision?.runs ?? [])
        .filter((item) => !item.deleted)
        .map((item) => item.run_id)
        .filter((item, index, arr) => arr.indexOf(item) === index),
    [selectedComparision],
  );

  const availableDetails = useMemo(
    () =>
      activeRunIds
        .map((runId) => generatedRunDetailById[runId])
        .filter((item): item is ProfilingGeneratedRun => Boolean(item)),
    [activeRunIds, generatedRunDetailById],
  );

  useEffect(() => {
    if (activeRunIds.length === 0) {
      setLoadingDetails(false);
      setDetailError("");
      return;
    }
    const missingRunIds = activeRunIds.filter((runId) => !generatedRunDetailById[runId]);
    if (missingRunIds.length === 0) {
      setLoadingDetails(false);
      setDetailError("");
      return;
    }

    let active = true;
    const load = async (): Promise<void> => {
      setLoadingDetails(true);
      setDetailError("");
      try {
        await Promise.all(missingRunIds.map((runId) => fetchGeneratedRunDetail(runId)));
        if (!active) {
          return;
        }
        const refreshed = useExperimentStore.getState().generatedRunDetailById;
        const stillMissing = missingRunIds.filter((runId) => !refreshed[runId]);
        if (stillMissing.length > 0) {
          setDetailError(`Unable to load details for run id(s): ${stillMissing.join(", ")}`);
        }
      } finally {
        if (active) {
          setLoadingDetails(false);
        }
      }
    };
    void load();

    return () => {
      active = false;
    };
  }, [activeRunIds, fetchGeneratedRunDetail, generatedRunDetailById]);

  useEffect(() => {
    if (availableDetails.length === 0) {
      setCurves([]);
      setCurveError("");
      setLoadingCurves(false);
      return;
    }

    const controller = new AbortController();
    const load = async (): Promise<void> => {
      setLoadingCurves(true);
      setCurveError("");
      const nextCurves: RttCurveLine[] = [];
      try {
        for (const detail of availableDetails) {
          const perRunCurves = await loadCurvesFromExperimentRun(detail, controller.signal);
          perRunCurves.forEach((curve) => {
            nextCurves.push({
              key: `${detail.id}-${curve.runId}`,
              runNumber: detail.id,
              label: `Run #${detail.id} / ${curve.runId} | payload=${curve.payload} | freq=${curve.frequency}`,
              frequencyHz: curve.frequencyHz,
              points: curve.points,
            });
          });
        }
        if (controller.signal.aborted) {
          return;
        }
        setCurves(nextCurves);
        if (nextCurves.length === 0) {
          setCurveError("No RTT curve data was found for this stored comparision.");
        }
      } catch (loadError) {
        if (controller.signal.aborted) {
          return;
        }
        setCurves([]);
        setCurveError(`Failed to load RTT data: ${String(loadError)}`);
      } finally {
        if (!controller.signal.aborted) {
          setLoadingCurves(false);
        }
      }
    };
    void load();

    return () => controller.abort();
  }, [availableDetails]);

  const measurementRows = useMemo<RunMeasurementStatsTableRow[]>(() => {
    const detailById = new Map<number, ProfilingGeneratedRun>();
    availableDetails.forEach((item) => {
      detailById.set(item.id, item);
    });

    const rows: RunMeasurementStatsTableRow[] = [];
    (selectedComparision?.runs ?? []).forEach((runItem) => {
      const detail = detailById.get(runItem.run_id);
      if (detail) {
        rows.push(buildRunMeasurementStatsRow(detail));
        return;
      }
      rows.push({
        runDbId: runItem.run_id,
        vmLink: "-",
        deleted: Boolean(runItem.deleted),
      });
    });
    return rows;
  }, [availableDetails, selectedComparision]);
  const artifactPathRows = useMemo(
    () =>
      availableDetails
        .flatMap((detail) => buildRunArtifactPathRows(detail))
        .sort((left, right) => (left.run_db_id === right.run_db_id ? left.run_id.localeCompare(right.run_id) : left.run_db_id - right.run_db_id)),
    [availableDetails],
  );
  const artifactPathJson = useMemo(() => JSON.stringify(artifactPathRows, null, 2), [artifactPathRows]);

  if (!Number.isFinite(comparisionId) || comparisionId <= 0) {
    return <EmptyState label="Invalid comparision id." />;
  }

  if (loading && comparisions.length === 0) {
    return <LoadingState label="Loading stored comparisions..." />;
  }

  if (error && comparisions.length === 0) {
    return <ErrorState message={error} />;
  }

  if (!selectedComparision) {
    return <EmptyState label={`Stored comparision ${comparisionId} was not found.`} />;
  }

  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1} alignItems={{ sm: "center" }}>
        <Stack spacing={0.4}>
          <Typography variant="h5">{selectedComparision.name}</Typography>
          <Typography variant="body2" color="text.secondary">
            {selectedComparision.description || "No description"}
          </Typography>
        </Stack>
        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            size="small"
            disabled={activeRunIds.length === 0}
            onClick={() => {
              setPathsCopyResult(null);
              setPathsDialogOpen(true);
            }}
          >
            Get Plot File Paths
          </Button>
          <Button component={RouterLink} to="/experiment/comparision_store" variant="outlined" size="small">
            Back to ComparisionStore
          </Button>
        </Stack>
      </Stack>

      <Paper sx={{ p: 1.5 }}>
        <Typography variant="subtitle2" gutterBottom>
          Stored Runs ({selectedComparision.run_count ?? selectedComparision.runs?.length ?? 0})
        </Typography>
        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
          {(selectedComparision.runs ?? []).map((runItem) => (
            runItem.deleted ? (
              <Chip
                key={`${selectedComparision.id}-${runItem.id}-${runItem.run_id}`}
                label={`Run #${runItem.run_id} (deleted)`}
                color="default"
                variant="outlined"
                size="small"
              />
            ) : (
              <Chip
                key={`${selectedComparision.id}-${runItem.id}-${runItem.run_id}`}
                label={`Run #${runItem.run_id} ${runItem.status ? `(${runItem.status})` : ""}`}
                color="primary"
                variant="filled"
                size="small"
                component={RouterLink}
                to={`/experiment/runs/${runItem.run_id}`}
                clickable
              />
            )
          ))}
        </Stack>
      </Paper>

      {loadingDetails ? <LoadingState label="Loading run details for stored comparision..." /> : null}
      {detailError ? <ErrorState message={detailError} /> : null}
      {loadingCurves ? <LoadingState label="Loading RTT curve data..." /> : null}
      {curveError && !loadingCurves ? <ErrorState message={curveError} /> : null}

      {!loadingCurves && curves.length > 0 ? (
        <RttCurveOverlay curves={curves} buildRunLink={(runId) => `/experiment/runs/${runId}`} />
      ) : null}

      <Paper sx={{ p: 1.5 }}>
        <Typography variant="subtitle2" gutterBottom>
          Run Comparison Statistics
        </Typography>
        <RunMeasurementStatsTable rows={measurementRows} />
      </Paper>
      <Dialog open={pathsDialogOpen} onClose={() => setPathsDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Plot Data File Paths</DialogTitle>
        <DialogContent>
          <Stack spacing={1} sx={{ pt: 0.5 }}>
            <Typography variant="body2" color="text.secondary">
              Includes file paths from database-backed run metrics directories.
            </Typography>
            {pathsCopyResult ? <Alert severity={pathsCopyResult.severity}>{pathsCopyResult.message}</Alert> : null}
            {loadingDetails ? (
              <Typography variant="body2" color="text.secondary">
                Loading run details...
              </Typography>
            ) : artifactPathRows.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No experiment JSON or latency CSV paths found for selected runs.
              </Typography>
            ) : (
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 1.5,
                  borderRadius: 1,
                  bgcolor: "grey.100",
                  color: "grey.900",
                  border: (theme) => `1px solid ${theme.palette.divider}`,
                  fontSize: 12,
                  lineHeight: 1.45,
                  overflowX: "auto",
                }}
              >
                {artifactPathJson}
              </Box>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button
            disabled={artifactPathRows.length === 0}
            onClick={async () => {
              try {
                if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
                  throw new Error("Clipboard API is unavailable in this browser.");
                }
                await navigator.clipboard.writeText(artifactPathJson);
                setPathsCopyResult({ severity: "success", message: "Copied plot file paths to clipboard." });
              } catch (error) {
                setPathsCopyResult({ severity: "error", message: `Copy failed: ${String(error)}` });
              }
            }}
          >
            Copy
          </Button>
          <Button onClick={() => setPathsDialogOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
