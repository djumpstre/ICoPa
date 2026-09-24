import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import {
  Alert,
  Button,
  Box,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useEffect, useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import { toErrorMessage } from "../../../core/api/errors";
import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { useExperimentStore } from "../../../stores/experiment.store";
import { useRunComparisonStore } from "../../../stores/runComparison.store";
import { RunMeasurementStatsTable } from "../components/RunMeasurementStatsTable";
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

export function ExperimentRunComparisionPage() {
  const selectedRunIds = useRunComparisonStore((state) => state.selectedRunIds);
  const removeRun = useRunComparisonStore((state) => state.removeRun);
  const clear = useRunComparisonStore((state) => state.clear);

  const generatedRunDetailById = useExperimentStore((state) => state.generatedRunDetailById);
  const fetchGeneratedRunDetail = useExperimentStore((state) => state.fetchGeneratedRunDetail);
  const createComparision = useExperimentStore((state) => state.createComparision);

  const [loadingDetails, setLoadingDetails] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [loadingCurves, setLoadingCurves] = useState(false);
  const [curveError, setCurveError] = useState("");
  const [curves, setCurves] = useState<RttCurveLine[]>([]);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [pathsDialogOpen, setPathsDialogOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveDescription, setSaveDescription] = useState("");
  const [saveLoading, setSaveLoading] = useState(false);
  const [saveResult, setSaveResult] = useState<{ severity: "success" | "error"; message: string } | null>(null);
  const [pathsCopyResult, setPathsCopyResult] = useState<{ severity: "success" | "error"; message: string } | null>(null);

  const handleCleanAll = (): void => {
    clear();
  };

  const selectedDetails = useMemo(
    () =>
      selectedRunIds
        .map((runId) => generatedRunDetailById[runId])
        .filter((detail): detail is ProfilingGeneratedRun => Boolean(detail)),
    [generatedRunDetailById, selectedRunIds],
  );

  const measurementRows = useMemo(
    () => selectedDetails.map((detail) => buildRunMeasurementStatsRow(detail)).sort((left, right) => right.runDbId - left.runDbId),
    [selectedDetails],
  );
  const artifactPathRows = useMemo(
    () =>
      selectedDetails
        .flatMap((detail) => buildRunArtifactPathRows(detail))
        .sort((left, right) => (left.run_db_id === right.run_db_id ? left.run_id.localeCompare(right.run_id) : left.run_db_id - right.run_db_id)),
    [selectedDetails],
  );
  const artifactPathJson = useMemo(() => JSON.stringify(artifactPathRows, null, 2), [artifactPathRows]);

  useEffect(() => {
    if (selectedRunIds.length === 0) {
      setLoadingDetails(false);
      setDetailError("");
      return;
    }

    const missingRunIds = selectedRunIds.filter((runId) => !generatedRunDetailById[runId]);
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
  }, [fetchGeneratedRunDetail, generatedRunDetailById, selectedRunIds]);

  useEffect(() => {
    if (selectedRunIds.length === 0) {
      setCurves([]);
      setCurveError("");
      setLoadingCurves(false);
      return;
    }
    if (selectedDetails.length === 0) {
      return;
    }

    const controller = new AbortController();
    const load = async (): Promise<void> => {
      setLoadingCurves(true);
      setCurveError("");
      const nextCurves: RttCurveLine[] = [];

      try {
        for (const runId of selectedRunIds) {
          const detail = selectedDetails.find((item) => item.id === runId);
          if (!detail) {
            continue;
          }
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
        if (nextCurves.length === 0) {
          setCurveError("No RTT curve data was found for the selected runs.");
        }
        setCurves(nextCurves);
      } catch (error) {
        if (controller.signal.aborted) {
          return;
        }
        setCurves([]);
        setCurveError(`Failed to load RTT data: ${String(error)}`);
      } finally {
        if (!controller.signal.aborted) {
          setLoadingCurves(false);
        }
      }
    };
    void load();

    return () => controller.abort();
  }, [selectedDetails, selectedRunIds]);

  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" spacing={1} alignItems={{ md: "center" }}>
        <Stack spacing={0.5}>
          <Typography variant="h5">Comparison</Typography>
          <Typography variant="body2" color="text.secondary">
            Compare RTT curves across selected generated runs.
          </Typography>
        </Stack>
        <Stack direction="row" spacing={1} flexWrap="wrap">
          <Button
            variant="contained"
            size="small"
            disabled={selectedRunIds.length === 0}
            onClick={() => {
              setSaveName("");
              setSaveDescription("");
              setSaveResult(null);
              setSaveDialogOpen(true);
            }}
          >
            Save To ComparisionStore
          </Button>
          <Button
            variant="outlined"
            size="small"
            disabled={selectedRunIds.length === 0}
            onClick={() => {
              setPathsCopyResult(null);
              setPathsDialogOpen(true);
            }}
          >
            Get Plot File Paths
          </Button>
          <Button component={RouterLink} to="/experiment/runs" variant="outlined" size="small">
            Add More Runs
          </Button>
          <Button variant="outlined" color="warning" size="small" onClick={handleCleanAll}>
            Clean All
          </Button>
          <Button variant="outlined" color="error" size="small" startIcon={<DeleteOutlineIcon />} onClick={clear}>
            Clear Selection
          </Button>
        </Stack>
      </Stack>

      {selectedRunIds.length === 0 ? (
        <EmptyState label="No runs in comparison set. Add runs from Experiment Runs or Run Detail page." />
      ) : (
        <>
          <Paper sx={{ p: 1.5 }}>
            <Typography variant="subtitle2" gutterBottom>
              Selected Generated Runs ({selectedRunIds.length})
            </Typography>
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
              {selectedRunIds.map((runId) => (
                <Chip
                  key={runId}
                  label={`Run #${runId}`}
                  onDelete={() => removeRun(runId)}
                  deleteIcon={<DeleteOutlineIcon />}
                  component={RouterLink}
                  to={`/experiment/runs/${runId}`}
                  clickable
                />
              ))}
            </Stack>
          </Paper>

          {loadingDetails && <LoadingState label="Loading selected run details..." />}
          {detailError && <ErrorState message={detailError} />}

          {loadingCurves ? <LoadingState label="Loading RTT curve data..." /> : null}
          {curveError && !loadingCurves ? <ErrorState message={curveError} /> : null}
          {saveResult ? <Alert severity={saveResult.severity}>{saveResult.message}</Alert> : null}

          {!loadingCurves && curves.length > 0 ? (
            <RttCurveOverlay curves={curves} buildRunLink={(runId) => `/experiment/runs/${runId}`} />
          ) : null}
          {measurementRows.length > 0 ? (
            <Paper sx={{ p: 1.5 }}>
              <Typography variant="subtitle2" gutterBottom>
                Run Comparison Statistics
              </Typography>
              <RunMeasurementStatsTable rows={measurementRows} />
            </Paper>
          ) : null}
        </>
      )}

      <Dialog open={saveDialogOpen} onClose={() => setSaveDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Save Comparision</DialogTitle>
        <DialogContent>
          <Stack spacing={1.2} sx={{ pt: 0.5 }}>
            <TextField
              label="Name"
              value={saveName}
              onChange={(event) => setSaveName(event.target.value)}
              size="small"
              placeholder="e.g. payload-16k-vs-32k"
              required
              autoFocus
            />
            <TextField
              label="Short Description"
              value={saveDescription}
              onChange={(event) => setSaveDescription(event.target.value)}
              size="small"
              multiline
              minRows={2}
              placeholder="Optional description"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveDialogOpen(false)} disabled={saveLoading}>
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={saveLoading || !saveName.trim() || selectedRunIds.length === 0}
            onClick={async () => {
              setSaveLoading(true);
              setSaveResult(null);
              try {
                const created = await createComparision({
                  name: saveName.trim(),
                  description: saveDescription.trim(),
                  runIds: selectedRunIds,
                });
                setSaveDialogOpen(false);
                setSaveResult({
                  severity: "success",
                  message: `Saved comparision '${created.name}' with ${selectedRunIds.length} run(s).`,
                });
              } catch (error) {
                setSaveResult({ severity: "error", message: toErrorMessage(error) });
              } finally {
                setSaveLoading(false);
              }
            }}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>
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
