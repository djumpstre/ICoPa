import CompareArrowsIcon from "@mui/icons-material/CompareArrows";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  Link as MuiLink,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";

import { ApiError, toErrorMessage } from "../../../core/api/errors";
import { requestArtifactJson, requestArtifactText } from "../../../core/api/artifacts";
import { StaleSessionError } from "../../../core/api/session";
import { env } from "../../../core/config/env";
import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { StatusBadge } from "../../../shared/components/StatusBadge";
import { formatDateTime } from "../../../shared/utils/date";
import { useAuthStore } from "../../../stores/auth.store";
import { useExperimentStore } from "../../../stores/experiment.store";
import { useRunComparisonStore } from "../../../stores/runComparison.store";
import { RttCurveOverlay, type RttCurveLine } from "../components/RttCurveOverlay";
import type { ProfilingGeneratedRun } from "../types";

type JsonRecord = Record<string, unknown>;

interface ActionView {
  index: number;
  type: string;
  target: string;
  preset: string;
  status: string;
  message: string;
  error: string;
  commandScript: string;
  commandSource: string;
}

interface PhaseView {
  index: number;
  name: string;
  mode: string;
  status: string;
  error: string;
  actions: ActionView[];
}

interface RunView {
  runId: string;
  status: string;
  runtimeEnvName: string;
  metricsBackendDir: string;
  metricsRemoteDir: string;
  error: string;
  parameters: JsonRecord;
  phases: PhaseView[];
}

interface RrtSummaryView {
  durationSec?: number;
  packetsSent?: number;
  packetsReceived?: number;
  packetsLost?: number;
  lossPercent?: number;
  latencyMinMs?: number;
  latencyP50Ms?: number;
  latencyP95Ms?: number;
  latencyP99Ms?: number;
  latencyMaxMs?: number;
  latencyAvgMs?: number;
}

interface LatencyPoint {
  id: number;
  rttMs: number;
}

interface RunMetricInsight {
  loading: boolean;
  error: string;
  summary: RrtSummaryView | null;
  configItems: Array<{ label: string; value: string }>;
  latencyPoints: LatencyPoint[];
  payloadBytes?: number;
  pubRateHz?: number;
  estUploadMbps?: number;
  estDownloadMbps?: number;
}

interface RunMetricFileUrls {
  summaryUrl: string;
  configUrl: string;
  allCsvUrl: string;
}

function asRecord(value: unknown): JsonRecord | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as JsonRecord;
}

function asRecordArray(value: unknown): JsonRecord[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is JsonRecord => !!asRecord(item));
}

function getString(value: unknown): string {
  if (typeof value === "string") {
    return value.trim();
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

function getNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
}

function getStatus(value: unknown): string {
  const text = getString(value);
  return text ? text.toUpperCase() : "UNKNOWN";
}

function extractActionCommand(actionValue: JsonRecord): { commandScript: string; commandSource: string } {
  const debug = asRecord(actionValue.debug);
  const candidates: Array<[string, unknown]> = [
    ["command_final", debug?.command_final],
    ["command_rendered", debug?.command_rendered],
    ["command", debug?.command],
  ];
  for (const [source, value] of candidates) {
    const command = getString(value);
    if (command) {
      return { commandScript: command, commandSource: source };
    }
  }
  return { commandScript: "", commandSource: "" };
}

function buildActionView(actionValue: JsonRecord, index: number): ActionView {
  const debug = asRecord(actionValue.debug);
  const debugPreset = asRecord(debug?.preset);
  const extracted = extractActionCommand(actionValue);
  return {
    index: Number(actionValue.index ?? index + 1),
    type: getString(actionValue.type),
    target: getString(actionValue.target),
    preset: getString(actionValue.preset || debugPreset?.name),
    status: getStatus(actionValue.status),
    message: getString(actionValue.message),
    error: getString(actionValue.error),
    commandScript: extracted.commandScript,
    commandSource: extracted.commandSource,
  };
}

function buildPhaseView(phaseValue: JsonRecord, index: number): PhaseView {
  return {
    index: Number(phaseValue.index ?? index + 1),
    name: getString(phaseValue.name) || `phase-${index + 1}`,
    mode: getString(phaseValue.mode) || "sequential",
    status: getStatus(phaseValue.status),
    error: getString(phaseValue.error),
    actions: asRecordArray(phaseValue.actions).map((actionValue, actionIndex) =>
      buildActionView(actionValue, actionIndex),
    ),
  };
}

function buildPlannedActionView(actionValue: JsonRecord, index: number): ActionView {
  return {
    index: Number(actionValue.index ?? index + 1),
    type: getString(actionValue.type),
    target: getString(actionValue.target),
    preset: getString(actionValue.preset),
    status: "PENDING",
    message: "",
    error: "",
    commandScript: "",
    commandSource: "",
  };
}

function buildPlannedPhaseView(phaseValue: JsonRecord, index: number): PhaseView {
  return {
    index: Number(phaseValue.index ?? index + 1),
    name: getString(phaseValue.name) || `phase-${index + 1}`,
    mode: getString(phaseValue.mode) || "sequential",
    status: "PENDING",
    error: "",
    actions: asRecordArray(phaseValue.actions).map((actionValue, actionIndex) =>
      buildPlannedActionView(actionValue, actionIndex),
    ),
  };
}


function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, "");
}

function getBackendOrigin(): string {
  try {
    return new URL(env.apiBaseUrl).origin;
  } catch {
    if (typeof window !== "undefined") {
      return window.location.origin;
    }
    return "";
  }
}

function toBackendUrl(pathOrUrl: string): string {
  const trimmed = getString(pathOrUrl);
  if (!trimmed) {
    return "";
  }
  if (/^https?:\/\//i.test(trimmed)) {
    return normalizeBaseUrl(trimmed);
  }
  const backendOrigin = getBackendOrigin();
  if (!backendOrigin) {
    return normalizeBaseUrl(trimmed);
  }
  if (trimmed.startsWith("/")) {
    return normalizeBaseUrl(`${backendOrigin}${trimmed}`);
  }
  const normalizedPath = trimmed.replace(/^\/+/, "");
  return normalizeBaseUrl(`${backendOrigin}/${normalizedPath}`);
}

function buildRunMetricBaseCandidates(detail: ProfilingGeneratedRun, run: RunView): string[] {
  const values = [
    detail.collected_metrics_url ? `${toBackendUrl(detail.collected_metrics_url)}/${run.runId}` : "",
    toBackendUrl(run.metricsBackendDir),
  ].filter((item) => !!item);
  return [...new Set(values)];
}

async function fetchTextIfOk(url: string, signal: AbortSignal): Promise<string> {
  return requestArtifactText(url, { signal, token: useAuthStore.getState().token });
}

async function fetchJsonIfOk(url: string, signal: AbortSignal): Promise<unknown | null> {
  return requestArtifactJson(url, { signal, token: useAuthStore.getState().token });
}

function parseRrtSummary(payload: unknown): RrtSummaryView | null {
  const summary = asRecord(payload);
  if (!summary) {
    return null;
  }
  const latency = asRecord(summary.latency_ms);

  return {
    durationSec: getNumber(summary.duration_sec),
    packetsSent: getNumber(summary.packets_sent),
    packetsReceived: getNumber(summary.packets_received),
    packetsLost: getNumber(summary.packets_lost),
    lossPercent: getNumber(summary.loss_percent),
    latencyMinMs: getNumber(latency?.min),
    latencyP50Ms: getNumber(latency?.p50),
    latencyP95Ms: getNumber(latency?.p95),
    latencyP99Ms: getNumber(latency?.p99),
    latencyMaxMs: getNumber(latency?.max),
    latencyAvgMs: getNumber(latency?.avg),
  };
}

function extractYamlScalar(yamlText: string, key: string): string {
  const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const regex = new RegExp(`^\\s*${escapedKey}:\\s*(.+)\\s*$`, "m");
  const match = yamlText.match(regex);
  return match ? match[1].trim().replace(/^['"]|['"]$/g, "") : "";
}

function parseImportantRrtConfig(yamlText: string): Array<{ label: string; value: string }> {
  if (!yamlText.trim()) {
    return [];
  }
  const keys: Array<[string, string]> = [
    ["frequency_hz", "Frequency (Hz)"],
    ["payload_size", "Payload Size"],
    ["response_payload_size", "Response Payload Size"],
    ["json_duration_sec", "Duration (s)"],
    ["reliability", "Reliability"],
    ["depth", "Depth"],
  ];
  return keys
    .map(([key, label]) => ({ label, value: extractYamlScalar(yamlText, key) }))
    .filter((item) => !!item.value);
}

function parseSizeToBytes(value: string): number | undefined {
  const text = getString(value);
  if (!text) {
    return undefined;
  }
  const match = text.match(/^([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)?$/);
  if (!match) {
    return undefined;
  }
  const amount = Number(match[1]);
  if (!Number.isFinite(amount) || amount < 0) {
    return undefined;
  }
  const unit = (match[2] || "B").toUpperCase();
  const factorByUnit: Record<string, number> = {
    B: 1,
    KB: 1_000,
    MB: 1_000_000,
    GB: 1_000_000_000,
    KIB: 1_024,
    MIB: 1_048_576,
    GIB: 1_073_741_824,
  };
  const factor = factorByUnit[unit];
  if (!factor) {
    return undefined;
  }
  return amount * factor;
}

function parseRateHz(value: string): number | undefined {
  const numeric = getNumber(value);
  if (numeric != null && numeric >= 0) {
    return numeric;
  }
  const text = getString(value);
  if (!text) {
    return undefined;
  }
  const match = text.match(/([0-9]+(?:\.[0-9]+)?)/);
  if (!match) {
    return undefined;
  }
  const parsed = Number(match[1]);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function estimateBandwidthMbps(payloadBytes?: number, pubRateHz?: number): number | undefined {
  if (payloadBytes == null || pubRateHz == null) {
    return undefined;
  }
  if (!Number.isFinite(payloadBytes) || !Number.isFinite(pubRateHz) || payloadBytes < 0 || pubRateHz < 0) {
    return undefined;
  }
  return (payloadBytes * pubRateHz * 8) / 1_000_000;
}

function estimateBandwidthFromConfig(yamlText: string): {
  payloadBytes?: number;
  pubRateHz?: number;
  estUploadMbps?: number;
  estDownloadMbps?: number;
} {
  if (!yamlText.trim()) {
    return {};
  }
  const payloadBytes = parseSizeToBytes(extractYamlScalar(yamlText, "payload_size"));
  const responsePayloadBytes = parseSizeToBytes(extractYamlScalar(yamlText, "response_payload_size"));
  const pubRateHz =
    parseRateHz(extractYamlScalar(yamlText, "frequency_hz")) ??
    parseRateHz(extractYamlScalar(yamlText, "publish_frequency_hz")) ??
    parseRateHz(extractYamlScalar(yamlText, "publish_frequency")) ??
    parseRateHz(extractYamlScalar(yamlText, "frequency"));
  return {
    payloadBytes,
    pubRateHz,
    estUploadMbps: estimateBandwidthMbps(payloadBytes, pubRateHz),
    estDownloadMbps: estimateBandwidthMbps(responsePayloadBytes, pubRateHz),
  };
}

function parseLatencyCsv(csvText: string): LatencyPoint[] {
  const rows = csvText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => !!line);
  if (rows.length === 0) {
    return [];
  }

  const parsedRows = rows.map((line) => line.split(",").map((cell) => cell.trim()));
  const header = parsedRows[0].map((cell) => cell.toLowerCase());
  const hasHeader = header.some((cell) => /[a-z]/i.test(cell));

  const toNumber = (value: string | undefined): number | null => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const pushPoints = (dataRows: string[][], xIndex: number, rttNsIndex: number): LatencyPoint[] => {
    const points: LatencyPoint[] = [];
    dataRows.forEach((cols) => {
      const xRaw = toNumber(cols[xIndex]);
      const rttNs = toNumber(cols[rttNsIndex]);
      if (xRaw == null || rttNs == null) {
        return;
      }
      points.push({
        id: xRaw,
        rttMs: rttNs / 1_000_000,
      });
    });
    return points;
  };

  if (hasHeader) {
    const timeIndex = header.indexOf("t_ns");
    const idIndex = header.indexOf("id");
    const rttNsIndex = header.indexOf("rtt_ns");
    const xIndex = timeIndex >= 0 ? timeIndex : idIndex;
    if (xIndex < 0 || rttNsIndex < 0) {
      return [];
    }
    return pushPoints(parsedRows.slice(1), xIndex, rttNsIndex);
  }

  const columnCount = parsedRows[0].length;
  if (columnCount !== 2 && columnCount !== 3) {
    return [];
  }
  const xIndex = 0;
  const rttNsIndex = columnCount - 1;
  return pushPoints(parsedRows, xIndex, rttNsIndex);
}


async function loadRunMetricInsight(
  detail: ProfilingGeneratedRun,
  run: RunView,
  signal: AbortSignal,
  fileUrls?: RunMetricFileUrls,
): Promise<RunMetricInsight> {
  const directSummaryUrl = toBackendUrl(fileUrls?.summaryUrl ?? "");
  const directConfigUrl = toBackendUrl(fileUrls?.configUrl ?? "");
  const directCsvUrl = toBackendUrl(fileUrls?.allCsvUrl ?? "");
  if (directSummaryUrl || directConfigUrl || directCsvUrl) {
    try {
      const [summaryPayload, configText, csvText] = await Promise.all([
        directSummaryUrl ? fetchJsonIfOk(directSummaryUrl, signal) : Promise.resolve(null),
        directConfigUrl ? fetchTextIfOk(directConfigUrl, signal) : Promise.resolve(""),
        directCsvUrl ? fetchTextIfOk(directCsvUrl, signal) : Promise.resolve(""),
      ]);
      if (summaryPayload || configText || csvText) {
        const bw = estimateBandwidthFromConfig(configText);
        return {
          loading: false,
          error: "",
          summary: parseRrtSummary(summaryPayload),
          configItems: parseImportantRrtConfig(configText),
          latencyPoints: parseLatencyCsv(csvText),
          ...bw,
        };
      }
    } catch (error) {
      if (error instanceof StaleSessionError || (error instanceof ApiError && error.status === 401) ||
          (error instanceof DOMException && error.name === "AbortError")) {
        throw error;
      }
    }
  }

  const candidates = buildRunMetricBaseCandidates(detail, run);

  for (const base of candidates) {
    const vmHomeBase = `${normalizeBaseUrl(base)}/vm_home`;
    const summaryUrl = `${vmHomeBase}/rrt_summary.json`;
    const configUrl = `${vmHomeBase}/rrt_config.yaml`;
    const csvUrl = `${vmHomeBase}/rrt_all.csv`;
    try {
      const summaryPayload = await fetchJsonIfOk(summaryUrl, signal);
      if (!summaryPayload) {
        continue;
      }
      const [configText, csvText] = await Promise.all([
        fetchTextIfOk(configUrl, signal),
        fetchTextIfOk(csvUrl, signal),
      ]);

      return {
        loading: false,
        error: "",
        summary: parseRrtSummary(summaryPayload),
        configItems: parseImportantRrtConfig(configText),
        latencyPoints: parseLatencyCsv(csvText),
        ...estimateBandwidthFromConfig(configText),
      };
    } catch (error) {
      if (error instanceof StaleSessionError || (error instanceof ApiError && error.status === 401) ||
          (error instanceof DOMException && error.name === "AbortError")) {
        throw error;
      }
    }
  }

  return {
    loading: false,
    error: "RRT metrics files were not found for this run.",
    summary: null,
    configItems: [],
    latencyPoints: [],
  };
}

function formatNumber(value: number | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) {
    return "-";
  }
  return value.toFixed(digits);
}

function formatBytesAbbr(value?: number): string {
  if (value == null || !Number.isFinite(value)) {
    return "-";
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)} MB`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(2)} KB`;
  }
  return `${value.toFixed(0)} B`;
}

function RunDetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "220px 1fr" }, gap: 1, py: 0.35 }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Box sx={{ typography: "body2", overflowWrap: "anywhere", wordBreak: "break-word" }}>
        {value}
      </Box>
    </Box>
  );
}

function CompactRow({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "1fr auto" },
        gap: 1,
        py: 0.2,
        ...(highlight
          ? { px: 0.8, borderRadius: 1, bgcolor: "rgba(35,181,156,0.12)", border: "1px solid rgba(35,181,156,0.35)" }
          : {}),
      }}
    >
      <Typography variant="body2" color={highlight ? "text.primary" : "text.secondary"} sx={highlight ? { fontWeight: 600 } : undefined}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ overflowWrap: "anywhere", wordBreak: "break-word", fontWeight: highlight ? 700 : 400 }}>
        {value}
      </Typography>
    </Box>
  );
}

function countActionsByStatus(actions: ActionView[], statuses: string[]): number {
  const allowed = new Set(statuses.map((item) => item.toUpperCase()));
  return actions.reduce((count, action) => count + (allowed.has(action.status.toUpperCase()) ? 1 : 0), 0);
}

function buildActionDebugScript(runValue: RunView, phaseValue: PhaseView, actionValue: ActionView): string {
  return [
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    "",
    `# run: ${runValue.runId}`,
    `# phase: ${phaseValue.name}`,
    `# action_index: ${actionValue.index}`,
    `# action_type: ${actionValue.type || "-"}`,
    `# target: ${actionValue.target || "-"}`,
    `# preset: ${actionValue.preset || "-"}`,
    `# status: ${actionValue.status || "-"}`,
    `# command_source: ${actionValue.commandSource || "-"}`,
    "",
    actionValue.commandScript,
    "",
  ].join("\n");
}

interface ActionScriptView {
  key: string;
  phaseName: string;
  phaseIndex: number;
  actionIndex: number;
  actionType: string;
  target: string;
  preset: string;
  status: string;
  commandSource: string;
  script: string;
}

interface RunScriptGroup {
  runId: string;
  runStatus: string;
  scripts: ActionScriptView[];
}

export function ExperimentRunDetailPage() {
  const params = useParams<{ runId: string }>();
  const runId = Number(params.runId);
  const navigate = useNavigate();

  const detail = useExperimentStore((state) => state.generatedRunDetailById[runId]);
  const loading = useExperimentStore((state) => state.loading);
  const error = useExperimentStore((state) => state.error);
  const fetchGeneratedRunDetail = useExperimentStore((state) => state.fetchGeneratedRunDetail);
  const rerunExperimentRun = useExperimentStore((state) => state.rerunExperimentRun);
  const deleteGeneratedRun = useExperimentStore((state) => state.deleteGeneratedRun);
  const updateGeneratedRunNote = useExperimentStore((state) => state.updateGeneratedRunNote);
  const selectedRunIds = useRunComparisonStore((state) => state.selectedRunIds);
  const toggleRun = useRunComparisonStore((state) => state.toggleRun);
  const removeRunFromComparison = useRunComparisonStore((state) => state.removeRun);
  const [runMetricsByRunId, setRunMetricsByRunId] = useState<Record<string, RunMetricInsight>>({});
  const [rerunDialogOpen, setRerunDialogOpen] = useState(false);
  const [rerunLoading, setRerunLoading] = useState(false);
  const [rerunReplaceCurrent, setRerunReplaceCurrent] = useState(false);
  const [rerunResult, setRerunResult] = useState<{ severity: "success" | "error"; message: string } | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteResult, setDeleteResult] = useState<{ severity: "success" | "error"; message: string } | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [noteSaving, setNoteSaving] = useState(false);
  const [noteResult, setNoteResult] = useState<{ severity: "success" | "error"; message: string } | null>(null);

  useEffect(() => {
    if (Number.isNaN(runId) || runId <= 0) {
      return;
    }
    void fetchGeneratedRunDetail(runId);
  }, [fetchGeneratedRunDetail, runId]);

  useEffect(() => {
    if (!detail) {
      setNoteDraft("");
      setNoteResult(null);
      return;
    }
    setNoteDraft(getString(detail.note));
    setNoteResult(null);
  }, [detail]);

  const runViews = useMemo<RunView[]>(() => {
    if (!detail) {
      return [];
    }

    const trackerRuns = asRecord(asRecord(detail.phase_action_execution_status)?.runs) ?? ({} as JsonRecord);
    const snapshotRuns = asRecord(asRecord(detail.rrt_config_execution_snapshot)?.runs) ?? ({} as JsonRecord);
    const output: RunView[] = [];
    const seen = new Set<string>();
    const compiledRuns = asRecordArray(asRecord(detail.compiled_payload_snapshot)?.runs);

    asRecordArray(detail.results).forEach((runValue, index) => {
      const runIdValue = getString(runValue.run_id) || `run-${String(index + 1).padStart(3, "0")}`;
      const trackerRun = asRecord(trackerRuns[runIdValue]);
      const snapshotRun = asRecord(snapshotRuns[runIdValue]);
      const resultPhases = asRecordArray(runValue.phases);
      const trackerPhases = asRecordArray(trackerRun?.phases);
      const phasesSource = resultPhases.length > 0 ? resultPhases : trackerPhases;

      const parameters: JsonRecord = {};
      const variantValues = asRecord(runValue.variant_values) ?? asRecord(snapshotRun?.variant_values);
      const autoRrtOverrides = asRecord(snapshotRun?.auto_rrt_overrides);
      const runtimeRrtPath = getString(snapshotRun?.runtime_rrt_config_path);
      if (variantValues && Object.keys(variantValues).length > 0) {
        parameters.variant_values = variantValues;
      }
      if (autoRrtOverrides && Object.keys(autoRrtOverrides).length > 0) {
        parameters.auto_rrt_overrides = autoRrtOverrides;
      }
      if (runtimeRrtPath) {
        parameters.runtime_rrt_config_path = runtimeRrtPath;
      }

      output.push({
        runId: runIdValue,
        status: getStatus(runValue.status || trackerRun?.status),
        runtimeEnvName: getString(runValue.runtime_env_name || trackerRun?.runtime_env_name),
        metricsBackendDir: getString(runValue.metrics_backend_dir),
        metricsRemoteDir: getString(runValue.metrics_remote_dir),
        error: getString(runValue.error || trackerRun?.error),
        parameters,
        phases: phasesSource.map((phaseValue, phaseIndex) => buildPhaseView(phaseValue, phaseIndex)),
      });
      seen.add(runIdValue);
    });

    Object.entries(trackerRuns).forEach(([runIdValue, trackerRunValue], index) => {
      if (seen.has(runIdValue)) {
        return;
      }
      const trackerRun = asRecord(trackerRunValue);
      const snapshotRun = asRecord(snapshotRuns[runIdValue]);
      const parameters: JsonRecord = {};
      const variantValues = asRecord(snapshotRun?.variant_values);
      const autoRrtOverrides = asRecord(snapshotRun?.auto_rrt_overrides);
      const runtimeRrtPath = getString(snapshotRun?.runtime_rrt_config_path);
      if (variantValues && Object.keys(variantValues).length > 0) {
        parameters.variant_values = variantValues;
      }
      if (autoRrtOverrides && Object.keys(autoRrtOverrides).length > 0) {
        parameters.auto_rrt_overrides = autoRrtOverrides;
      }
      if (runtimeRrtPath) {
        parameters.runtime_rrt_config_path = runtimeRrtPath;
      }

      output.push({
        runId: runIdValue || `run-${String(index + 1).padStart(3, "0")}`,
        status: getStatus(trackerRun?.status),
        runtimeEnvName: getString(trackerRun?.runtime_env_name),
        metricsBackendDir: "",
        metricsRemoteDir: "",
        error: getString(trackerRun?.error),
        parameters,
        phases: asRecordArray(trackerRun?.phases).map((phaseValue, phaseIndex) => buildPhaseView(phaseValue, phaseIndex)),
      });
    });

    compiledRuns.forEach((compiledRunValue, index) => {
      const runIdValue = getString(compiledRunValue.run_id) || `run-${String(index + 1).padStart(3, "0")}`;
      if (seen.has(runIdValue)) {
        return;
      }
      output.push({
        runId: runIdValue,
        status: "PENDING",
        runtimeEnvName: getString(compiledRunValue.runtime_env_name),
        metricsBackendDir: "",
        metricsRemoteDir: "",
        error: "",
        parameters: asRecord(compiledRunValue.variant_values) ?? {},
        phases: asRecordArray(compiledRunValue.phases).map((phaseValue, phaseIndex) =>
          buildPlannedPhaseView(phaseValue, phaseIndex),
        ),
      });
      seen.add(runIdValue);
    });

    return output.sort((left, right) => left.runId.localeCompare(right.runId));
  }, [detail]);

  const runMetricRows = useMemo(
    () => {
      const metricRunIds = new Set(
        asRecordArray(detail?.run_metric_files)
          .map((item) => getString(item.run_id))
          .filter((value) => !!value),
      );
      const runtimeConfigRunIds = new Set(
        asRecordArray(detail?.rrt_configs)
          .map((item) => getString(item.run_id))
          .filter((value) => !!value),
      );
      return runViews.filter(
        (runValue) =>
          runValue.metricsBackendDir ||
          runValue.metricsRemoteDir ||
          metricRunIds.has(runValue.runId) ||
          runtimeConfigRunIds.has(runValue.runId) ||
          !!getString(runValue.parameters.runtime_rrt_config_path),
      );
    },
    [detail, runViews],
  );

  const runMetricFileUrlMap = useMemo<Record<string, RunMetricFileUrls>>(() => {
    const map: Record<string, RunMetricFileUrls> = {};
    if (!detail) {
      return map;
    }
    asRecordArray(detail.run_metric_files).forEach((item) => {
      const runIdValue = getString(item.run_id);
      if (!runIdValue) {
        return;
      }
      map[runIdValue] = {
        summaryUrl: getString(item.rrt_summary_url),
        configUrl: getString(item.rrt_config_url),
        allCsvUrl: getString(item.rrt_all_csv_url),
      };
    });
    return map;
  }, [detail]);

  const runtimeRrtConfigByRunId = useMemo<
    Record<
      string,
      {
        path: string;
        generatedPath: string;
        generatedUrl: string;
        configYaml: string;
        configJson: unknown;
      }
    >
  >(() => {
    const map: Record<
      string,
      {
        path: string;
        generatedPath: string;
        generatedUrl: string;
        configYaml: string;
        configJson: unknown;
      }
    > = {};
    if (!detail) {
      return map;
    }
    asRecordArray(detail.rrt_configs).forEach((item) => {
      const runIdValue = getString(item.run_id);
      if (!runIdValue) {
        return;
      }
      map[runIdValue] = {
        path: getString(item.runtime_rrt_config_path),
        generatedPath: getString(item.generated_rrt_config_backend_path),
        generatedUrl: getString(item.generated_rrt_config_url),
        configYaml: getString(item.rrt_config_yaml),
        configJson: item.rrt_config_json ?? {},
      };
    });
    return map;
  }, [detail]);

  const metricFetchRows = useMemo(
    () =>
      runMetricRows.filter((runValue) => {
        const files = runMetricFileUrlMap[runValue.runId];
        return Boolean(
          runValue.metricsBackendDir ||
            runValue.metricsRemoteDir ||
            getString(files?.summaryUrl) ||
            getString(files?.configUrl) ||
            getString(files?.allCsvUrl),
        );
      }),
    [runMetricRows, runMetricFileUrlMap],
  );

  const latencyCurves = useMemo<RttCurveLine[]>(() => {
    if (!detail) {
      return [];
    }
    return metricFetchRows
      .map((runValue) => {
        const insight = runMetricsByRunId[runValue.runId];
        if (!insight || insight.loading || insight.error || insight.latencyPoints.length === 0) {
          return null;
        }
        return {
          key: `${detail.id}-${runValue.runId}`,
          runNumber: detail.id,
          label: `${runValue.runId}`,
          frequencyHz: insight.pubRateHz,
          points: insight.latencyPoints,
        } as RttCurveLine;
      })
      .filter((curve): curve is RttCurveLine => Boolean(curve));
  }, [detail, metricFetchRows, runMetricsByRunId]);

  const latencyCurvesLoading = useMemo(
    () => metricFetchRows.some((runValue) => Boolean(runMetricsByRunId[runValue.runId]?.loading)),
    [metricFetchRows, runMetricsByRunId],
  );

  const runScriptGroups = useMemo<RunScriptGroup[]>(
    () =>
      runViews
        .map((runValue) => {
          const scripts: ActionScriptView[] = [];
          runValue.phases.forEach((phaseValue) => {
            phaseValue.actions.forEach((actionValue) => {
              if (!actionValue.commandScript) {
                return;
              }
              scripts.push({
                key: `${runValue.runId}-${phaseValue.index}-${actionValue.index}-${actionValue.type}`,
                phaseName: phaseValue.name,
                phaseIndex: phaseValue.index,
                actionIndex: actionValue.index,
                actionType: actionValue.type,
                target: actionValue.target,
                preset: actionValue.preset,
                status: actionValue.status,
                commandSource: actionValue.commandSource,
                script: buildActionDebugScript(runValue, phaseValue, actionValue),
              });
            });
          });
          return {
            runId: runValue.runId,
            runStatus: runValue.status,
            scripts,
          };
        })
        .filter((item) => item.scripts.length > 0),
    [runViews],
  );

  useEffect(() => {
    if (!detail || metricFetchRows.length === 0) {
      return;
    }

    const controller = new AbortController();
    const load = async (): Promise<void> => {
      setRunMetricsByRunId((prev) => {
        const next = { ...prev };
        metricFetchRows.forEach((runValue) => {
          next[runValue.runId] = {
            loading: true,
            error: "",
            summary: null,
            configItems: [],
            latencyPoints: [],
          };
        });
        return next;
      });

      try {
        const pairs = await Promise.all(
          metricFetchRows.map(async (runValue) => {
            const insight = await loadRunMetricInsight(
              detail,
              runValue,
              controller.signal,
              runMetricFileUrlMap[runValue.runId],
            );
            return [runValue.runId, insight] as const;
          }),
        );
        if (controller.signal.aborted) {
          return;
        }
        setRunMetricsByRunId((prev) => {
          const next = { ...prev };
          pairs.forEach(([id, insight]) => {
            next[id] = insight;
          });
          return next;
        });
      } catch (loadError) {
        if (controller.signal.aborted) {
          return;
        }
        const fallback = `Unable to load run metric artifacts: ${String(loadError)}`;
        setRunMetricsByRunId((prev) => {
          const next = { ...prev };
          metricFetchRows.forEach((runValue) => {
            next[runValue.runId] = {
              loading: false,
              error: fallback,
              summary: null,
              configItems: [],
              latencyPoints: [],
            };
          });
          return next;
        });
      }
    };
    void load();

    return () => controller.abort();
  }, [detail, metricFetchRows, runMetricFileUrlMap]);

  if (!Number.isFinite(runId) || runId <= 0) {
    return <EmptyState label="Invalid run id." />;
  }

  if (loading && !detail) {
    return <LoadingState label="Loading experiment run detail..." />;
  }

  if (error && !detail) {
    return <ErrorState message={error} />;
  }

  if (!detail) {
    return <EmptyState label={`Experiment run ${runId} was not found.`} />;
  }

  const runStatusUpper = getStatus(detail.status);
  const rerunDisabled = rerunLoading || runStatusUpper === "RUNNING";
  const deleteDisabled =
    deleteLoading || runStatusUpper === "RUNNING" || runStatusUpper === "PENDING";
  const noteDirty = noteDraft !== getString(detail.note);
  const handleRerun = async (): Promise<void> => {
    setRerunResult(null);
    setRerunLoading(true);
    try {
      const payload = await rerunExperimentRun(detail.id, { replaceCurrentRun: rerunReplaceCurrent });
      const message = getString(payload.message) || `Re-run created from run #${detail.id}.`;
      setRerunResult({ severity: "success", message });
      const rerunRunId = Number(payload.rerun_run_id ?? 0);
      if (Number.isFinite(rerunRunId) && rerunRunId > 0) {
        navigate(`/experiment/runs/${rerunRunId}`);
      }
    } catch (rerunError) {
      setRerunResult({ severity: "error", message: toErrorMessage(rerunError) });
    } finally {
      setRerunLoading(false);
    }
  };
  const handleSaveNote = async (): Promise<void> => {
    setNoteResult(null);
    setNoteSaving(true);
    try {
      const updatedRun = await updateGeneratedRunNote(detail.id, noteDraft);
      setNoteDraft(getString(updatedRun.note));
      setNoteResult({ severity: "success", message: `Saved note for run #${detail.id}.` });
    } catch (saveError) {
      setNoteResult({ severity: "error", message: toErrorMessage(saveError) });
    } finally {
      setNoteSaving(false);
    }
  };
  const handleDeleteRun = async (): Promise<void> => {
    setDeleteResult(null);
    setDeleteLoading(true);
    try {
      const payload = await deleteGeneratedRun(detail.id);
      removeRunFromComparison(detail.id);
      const message = getString(payload.message) || `Deleted run #${detail.id}.`;
      setDeleteResult({ severity: "success", message });
      navigate("/experiment/runs");
    } catch (deleteError) {
      setDeleteResult({ severity: "error", message: toErrorMessage(deleteError) });
    } finally {
      setDeleteLoading(false);
    }
  };

  return (
    <Stack
      spacing={2}
      sx={{
        width: "100%",
        minWidth: 0,
        maxWidth: "100%",
        overflowX: "hidden",
        "& > *": { minWidth: 0, maxWidth: "100%" },
        "& .MuiPaper-root": { minWidth: 0, maxWidth: "100%" },
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        spacing={1}
        sx={{ minWidth: 0 }}
      >
          <Typography variant="h5" sx={{ minWidth: 0 }}>
            Experiment Run Detail
          </Typography>
          <Box
            sx={{
              width: { xs: "100%", sm: "auto" },
              minWidth: 0,
              display: "flex",
              flexWrap: "wrap",
              gap: 1,
              alignItems: "center",
              justifyContent: { xs: "flex-start", sm: "flex-end" },
            }}
          >
            <Button
              variant={selectedRunIds.includes(detail.id) ? "contained" : "outlined"}
              size="small"
              startIcon={<CompareArrowsIcon fontSize="small" />}
              onClick={() => toggleRun(detail.id)}
            >
              {selectedRunIds.includes(detail.id) ? "Remove From Comparison" : "Add To Comparison"}
            </Button>
            <Button component={RouterLink} to="/experiment/comparison" variant="outlined" size="small">
              Comparison ({selectedRunIds.length})
            </Button>
            <Button
              variant="contained"
              color="warning"
              size="small"
              onClick={() => {
                setRerunReplaceCurrent(false);
                setRerunDialogOpen(true);
              }}
              disabled={rerunDisabled}
            >
              {rerunLoading ? "Re-running..." : "Re-run This Run"}
            </Button>
            <Button
              variant="outlined"
              color="error"
              size="small"
              onClick={() => setDeleteDialogOpen(true)}
              disabled={deleteDisabled}
            >
              {deleteLoading ? "Deleting..." : "Delete Run"}
            </Button>
            <Button component={RouterLink} to="/experiment/runs" variant="outlined" size="small">
              Back to list
            </Button>
          </Box>
        </Stack>
        {error && <ErrorState message={error} />}
        {rerunResult ? <Alert severity={rerunResult.severity}>{rerunResult.message}</Alert> : null}
        {deleteResult ? <Alert severity={deleteResult.severity}>{deleteResult.message}</Alert> : null}
        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            Run #{detail.id}
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="body2">Status:</Typography>
            <StatusBadge value={detail.status} />
          </Stack>
          <RunDetailRow
            label="Experiment"
            value={detail.experiment_name ?? detail.generated_plan_name ?? "-"}
          />
          <RunDetailRow label="Generation" value={detail.gen_version ? `v${detail.gen_version}` : "-"} />
          <RunDetailRow label="Stop On Failure" value={detail.stop_on_failure ? "true" : "false"} />
          <RunDetailRow label="Started" value={formatDateTime(detail.started_at)} />
          <RunDetailRow label="Finished" value={formatDateTime(detail.finished_at)} />
          <Stack spacing={1} sx={{ mt: 1 }}>
            <Typography variant="subtitle2">Comparison Note</Typography>
            <TextField
              size="small"
              multiline
              minRows={2}
              maxRows={8}
              placeholder="Add a note to describe why this run should be compared."
              value={noteDraft}
              onChange={(event) => setNoteDraft(event.target.value)}
              inputProps={{ maxLength: 4000 }}
              disabled={noteSaving}
              fullWidth
            />
            <Stack direction="row" spacing={1} alignItems="center">
              <Button
                variant="contained"
                size="small"
                onClick={() => void handleSaveNote()}
                disabled={noteSaving || !noteDirty}
              >
                {noteSaving ? "Saving..." : "Save Note"}
              </Button>
              <Button
                variant="text"
                size="small"
                disabled={noteSaving || !noteDirty}
                onClick={() => setNoteDraft(getString(detail.note))}
              >
                Reset
              </Button>
            </Stack>
            {noteResult ? <Alert severity={noteResult.severity}>{noteResult.message}</Alert> : null}
          </Stack>
        </Paper>

        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            RTT Curves Overlay
          </Typography>
          {metricFetchRows.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No run-specific metric locations recorded yet.
            </Typography>
          ) : latencyCurvesLoading ? (
            <LoadingState label="Loading RTT curve data..." />
          ) : latencyCurves.length > 0 ? (
            <RttCurveOverlay curves={latencyCurves} />
          ) : (
            <Typography variant="body2" color="text.secondary">
              RTT curves are not available from collected metrics.
            </Typography>
          )}
        </Paper>

        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            Run Process Overview
          </Typography>
          {runViews.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No run process data is available yet.
            </Typography>
          ) : (
            <Stack spacing={1}>
              {runViews.map((runValue) => (
                <Paper key={`overview-${runValue.runId}`} variant="outlined" sx={{ p: 1.1 }}>
                  <Stack
                    direction={{ xs: "column", sm: "row" }}
                    spacing={1}
                    alignItems={{ xs: "flex-start", sm: "center" }}
                    justifyContent="space-between"
                  >
                    <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                      {runValue.runId}
                    </Typography>
                    <StatusBadge value={runValue.status} />
                  </Stack>
                  <Typography variant="body2" color="text.secondary">
                    Runtime: {runValue.runtimeEnvName || "-"}
                  </Typography>
                  {runValue.error ? (
                    <Typography variant="body2" color="error.main">
                      Error: {runValue.error}
                    </Typography>
                  ) : null}
                  {runValue.phases.length === 0 ? (
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7 }}>
                      No phase data recorded yet.
                    </Typography>
                  ) : (
                    <Stack spacing={0.7} sx={{ mt: 0.8 }}>
                      {runValue.phases.map((phaseValue) => {
                        const totalActions = phaseValue.actions.length;
                        const succeededActions = countActionsByStatus(phaseValue.actions, ["SUCCEEDED", "SUCCESS"]);
                        const runningActions = countActionsByStatus(phaseValue.actions, ["RUNNING"]);
                        const failedActions = countActionsByStatus(phaseValue.actions, ["FAILED", "ERROR", "FAIL"]);
                        return (
                          <Paper
                            key={`overview-${runValue.runId}-phase-${phaseValue.index}`}
                            variant="outlined"
                            sx={{ p: 0.9 }}
                          >
                            <Stack
                              direction={{ xs: "column", sm: "row" }}
                              spacing={1}
                              alignItems={{ xs: "flex-start", sm: "center" }}
                              justifyContent="space-between"
                            >
                              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                                {phaseValue.index}. {phaseValue.name}
                              </Typography>
                              <StatusBadge value={phaseValue.status} />
                            </Stack>
                            <Typography variant="body2" color="text.secondary">
                              Actions: {succeededActions}/{totalActions} succeeded, {runningActions} running, {failedActions} failed
                            </Typography>
                            {phaseValue.error ? (
                              <Typography variant="body2" color="error.main">
                                {phaseValue.error}
                              </Typography>
                            ) : null}
                          </Paper>
                        );
                      })}
                    </Stack>
                  )}
                </Paper>
              ))}
            </Stack>
          )}
        </Paper>

        <Dialog open={rerunDialogOpen} onClose={() => setRerunDialogOpen(false)} maxWidth="sm" fullWidth>
          <DialogTitle>Re-run This Run?</DialogTitle>
          <DialogContent>
            <DialogContentText>
              Re-running starts execution immediately.
            </DialogContentText>
            <FormControlLabel
              sx={{ mt: 1 }}
              control={
                <Checkbox
                  checked={rerunReplaceCurrent}
                  onChange={(event) => setRerunReplaceCurrent(event.target.checked)}
                  disabled={rerunLoading}
                />
              }
              label="Replace current run id (reuse this run, reset status/outputs, do not create a new generation)"
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setRerunDialogOpen(false)} disabled={rerunLoading}>
              Cancel
            </Button>
            <Button
              variant="contained"
              color="warning"
              disabled={rerunDisabled}
              onClick={() => {
                setRerunDialogOpen(false);
                void handleRerun();
              }}
            >
              {rerunReplaceCurrent ? "Reset and Re-run" : "Start Re-run"}
            </Button>
          </DialogActions>
        </Dialog>
        <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)} maxWidth="xs" fullWidth>
          <DialogTitle>Delete This Run?</DialogTitle>
          <DialogContent>
            <DialogContentText>
              This permanently removes run #{detail.id} from the database and deletes its stored artifacts.
            </DialogContentText>
            <DialogContentText sx={{ mt: 1 }}>
              This action cannot be undone.
            </DialogContentText>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setDeleteDialogOpen(false)} disabled={deleteLoading}>
              Cancel
            </Button>
            <Button
              variant="contained"
              color="error"
              disabled={deleteDisabled}
              onClick={() => {
                setDeleteDialogOpen(false);
                void handleDeleteRun();
              }}
            >
              Delete
            </Button>
          </DialogActions>
        </Dialog>

        {detail.error_message && (
          <Paper sx={{ p: 2 }}>
            <Typography variant="h6" color="error" gutterBottom>
              Error
            </Typography>
            <Typography variant="body2" color="error.main">
              {detail.error_message}
            </Typography>
          </Paper>
        )}

        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            Collected Metrics
          </Typography>
          <RunDetailRow
            label="Metrics"
            value={
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                <StatusBadge value={detail.metrics_collected ? "SUCCEEDED" : "FAILED"} />
                {detail.collected_metrics_url ? (
                  <MuiLink
                    href={detail.collected_metrics_url}
                    target="_blank"
                    rel="noreferrer"
                    sx={{ overflowWrap: "anywhere", wordBreak: "break-word" }}
                  >
                    {detail.collected_metrics_url}
                  </MuiLink>
                ) : (
                  <Typography variant="body2">-</Typography>
                )}
              </Stack>
            }
          />
          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
            Success Badge indicates metric collection status.
          </Typography>
          {runMetricRows.length > 0 ? (
            <Stack spacing={1} sx={{ mt: 1.5 }}>
              <Typography variant="subtitle2">Per-run RRT metrics</Typography>
              {runMetricRows.map((runValue) => {
                const insight = runMetricsByRunId[runValue.runId];
                const runtimeConfig = runtimeRrtConfigByRunId[runValue.runId];
                const runtimeConfigPath = getString(runValue.parameters.runtime_rrt_config_path);
                const resolvedRuntimeConfigPath = runtimeConfig?.generatedPath || runtimeConfig?.path || runtimeConfigPath;
                const metricFiles = runMetricFileUrlMap[runValue.runId];
                const hasMetricSources = Boolean(
                  runValue.metricsBackendDir ||
                    runValue.metricsRemoteDir ||
                    getString(metricFiles?.summaryUrl) ||
                    getString(metricFiles?.configUrl) ||
                    getString(metricFiles?.allCsvUrl),
                );
                return (
                  <Paper key={`metric-${runValue.runId}`} variant="outlined" sx={{ p: 1.2 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                      {runValue.runId}
                    </Typography>
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ overflowWrap: "anywhere", wordBreak: "break-word" }}
                    >
                      Backend: {runValue.metricsBackendDir || "-"}
                    </Typography>
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ overflowWrap: "anywhere", wordBreak: "break-word" }}
                    >
                      Remote: {runValue.metricsRemoteDir || "-"}
                    </Typography>

                    {!hasMetricSources ? (
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                        Metric artifacts are not available yet for this run.
                      </Typography>
                    ) : !insight || insight.loading ? (
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                        Loading `rrt_summary.json`, `rrt_config.yaml`, and `rrt_all.csv`...
                      </Typography>
                    ) : insight.error ? (
                      <Typography variant="body2" color="error.main" sx={{ mt: 1 }}>
                        {insight.error}
                      </Typography>
                    ) : (
                      <Stack spacing={1} sx={{ mt: 1 }}>
                        <Box
                          sx={{
                            display: "grid",
                            gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
                            gap: 1,
                          }}
                        >
                          <Paper variant="outlined" sx={{ p: 1 }}>
                            <Typography variant="subtitle2" gutterBottom>
                              RRT Config (Important)
                            </Typography>
                            {insight.configItems.length === 0 ? (
                              <Typography variant="body2" color="text.secondary">
                                Config not available.
                              </Typography>
                            ) : (
                              insight.configItems.map((item) => (
                                <CompactRow key={`${runValue.runId}-${item.label}`} label={item.label} value={item.value} />
                              ))
                            )}
                            <CompactRow label="PL" value={formatBytesAbbr(insight.payloadBytes)} />
                            <CompactRow label="PR" value={formatNumber(insight.pubRateHz, 2)} />
                            <CompactRow label="Up BW (Mbps)" value={formatNumber(insight.estUploadMbps, 3)} />
                            <CompactRow label="Down BW (Mbps)" value={formatNumber(insight.estDownloadMbps, 3)} />
                          </Paper>

                          <Paper variant="outlined" sx={{ p: 1 }}>
                            <Typography variant="subtitle2" gutterBottom>
                              RRT Summary
                            </Typography>
                            {insight.summary ? (
                              <>
                                <CompactRow label="Duration (s)" value={formatNumber(insight.summary.durationSec, 2)} />
                                <CompactRow
                                  label="Packets (sent/recv/lost)"
                                  value={`${insight.summary.packetsSent ?? "-"} / ${insight.summary.packetsReceived ?? "-"} / ${insight.summary.packetsLost ?? "-"}`}
                                />
                                <CompactRow
                                  label="Loss (%)"
                                  value={formatNumber(insight.summary.lossPercent, 3)}
                                />
                                <CompactRow
                                  label="Latency ms (p50/p95/p99)"
                                  value={`${formatNumber(insight.summary.latencyP50Ms)} / ${formatNumber(insight.summary.latencyP95Ms)} / ${formatNumber(insight.summary.latencyP99Ms)}`}
                                  highlight
                                />
                                <CompactRow
                                  label="Latency ms (min/max/avg)"
                                  value={`${formatNumber(insight.summary.latencyMinMs)} / ${formatNumber(insight.summary.latencyMaxMs)} / ${formatNumber(insight.summary.latencyAvgMs)}`}
                                  highlight
                                />
                                <CompactRow label="RTT Samples" value={String(insight.latencyPoints.length)} />
                              </>
                            ) : (
                              <Typography variant="body2" color="text.secondary">
                                Summary not available.
                              </Typography>
                            )}
                          </Paper>
                        </Box>

                      </Stack>
                    )}
                    {resolvedRuntimeConfigPath || runtimeConfig?.generatedUrl ? (
                      <Paper variant="outlined" sx={{ p: 1, mt: 1 }}>
                        <Typography variant="subtitle2" gutterBottom>
                          Runtime RRT Config (JSON / YAML)
                        </Typography>
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                          <Box component="span" sx={{ overflowWrap: "anywhere", wordBreak: "break-word" }}>
                            Path: {resolvedRuntimeConfigPath || "-"}
                          </Box>
                        </Typography>
                        {runtimeConfig?.generatedUrl ? (
                          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                            Generated File:{" "}
                            <MuiLink
                              href={runtimeConfig.generatedUrl}
                              target="_blank"
                              rel="noreferrer"
                              sx={{ overflowWrap: "anywhere", wordBreak: "break-word" }}
                            >
                              {runtimeConfig.generatedUrl}
                            </MuiLink>
                          </Typography>
                        ) : null}
                        {!runtimeConfig ? (
                          <Typography variant="body2" color="text.secondary">
                            Runtime RRT config is not available in this run detail response.
                          </Typography>
                        ) : (
                          <Stack spacing={1}>
                            <Paper variant="outlined" sx={{ p: 1.2, bgcolor: "background.default" }}>
                              <Typography
                                component="pre"
                                variant="body2"
                                sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word", m: 0 }}
                              >
                                {JSON.stringify(runtimeConfig.configJson ?? {}, null, 2)}
                              </Typography>
                            </Paper>
                            {runtimeConfig.configYaml ? (
                              <Paper variant="outlined" sx={{ p: 1.2, bgcolor: "background.default" }}>
                                <Typography
                                  component="pre"
                                  variant="body2"
                                  sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word", m: 0 }}
                                >
                                  {runtimeConfig.configYaml}
                                </Typography>
                              </Paper>
                            ) : null}
                          </Stack>
                        )}
                      </Paper>
                    ) : null}
                  </Paper>
                );
              })}
            </Stack>
          ) : (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              No run-specific metric locations recorded yet.
            </Typography>
          )}
        </Paper>

        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            Execution Bash Scripts
          </Typography>
          {runScriptGroups.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No executed shell commands were captured for this run.
            </Typography>
          ) : (
            <Stack spacing={1}>
              {runScriptGroups.map((group) => (
                <Paper key={`scripts-${group.runId}`} variant="outlined" sx={{ p: 1.2 }}>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.8 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                      {group.runId}
                    </Typography>
                    <StatusBadge value={group.runStatus} />
                  </Stack>
                  <Stack spacing={0.9}>
                    {group.scripts.map((scriptValue) => (
                      <Box key={scriptValue.key}>
                        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 0.4 }}>
                          Phase {scriptValue.phaseIndex}: {scriptValue.phaseName} | Action {scriptValue.actionIndex} |{" "}
                          {scriptValue.actionType || "-"}@{scriptValue.target || "-"} | Status: {scriptValue.status} | Source:{" "}
                          {scriptValue.commandSource || "-"}
                        </Typography>
                        <Box
                          component="pre"
                          sx={{
                            m: 0,
                            p: 1,
                            border: "1px solid",
                            borderColor: "divider",
                            borderRadius: 1,
                            bgcolor: "rgba(0,0,0,0.04)",
                            overflowX: "auto",
                            minWidth: 0,
                            maxWidth: "100%",
                            fontSize: 12,
                            lineHeight: 1.35,
                            fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace",
                          }}
                        >
                          {scriptValue.script}
                        </Box>
                      </Box>
                    ))}
                  </Stack>
                </Paper>
              ))}
            </Stack>
          )}
        </Paper>

    </Stack>
  );
}
