import CompareArrowsIcon from "@mui/icons-material/CompareArrows";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  MenuItem,
  Paper,
  TextField,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Typography,
} from "@mui/material";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { toErrorMessage } from "../../../core/api/errors";
import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { StatusBadge } from "../../../shared/components/StatusBadge";
import { formatDateTime, formatRelativeSinceNow } from "../../../shared/utils/date";
import { useExperimentStore } from "../../../stores/experiment.store";
import { useRunComparisonStore } from "../../../stores/runComparison.store";
import { RttCurveOverlay, type RttCurveLine } from "../components/RttCurveOverlay";
import { loadCurvesFromExperimentRun } from "../components/rrtCurveData";
import type { ProfilingGeneratedRun } from "../types";

type JsonRecord = Record<string, unknown>;

interface ActionView {
  index: number;
  type: string;
  target: string;
  details: Array<{ label: string; value: string }>;
}

interface PhaseView {
  index: number;
  name: string;
  mode: string;
  template: string;
  details: Array<{ label: string; value: string }>;
  actions: ActionView[];
}

interface RunComparisonStatsRow {
  runDbId: number;
  vmLink: string;
  payloadBytes?: number;
  pubRateHz?: number;
  estUploadMbps?: number;
  estDownloadMbps?: number;
  meanRttMs?: number;
  p95Ms?: number;
  p99Ms?: number;
  packetLossPercent?: number;
}

interface GenerationSummaryRow {
  genVersion: number;
  total: number;
  pending: number;
  running: number;
  succeeded: number;
  failed: number;
}

interface RunSpecificsView {
  payload: string;
  publishRate: string;
  runtimeEnv: string;
  link: string;
}

interface ReplaceableGenerationOption {
  genVersion: number;
  total: number;
  pendingCount: number;
  runningCount: number;
  succeededCount: number;
  failedCount: number;
  selectable: boolean;
}

interface TopologyNodeView {
  name: string;
  reachability: string;
  containerRuntimeReady: boolean;
  managedContainersRunningCount: number;
}

interface TopologyEdgeView {
  name: string;
  from: string;
  to: string;
  link: string;
  validationStatus: string;
  checkedAt: string;
  pingLatencyMs?: number;
}

type StatsSortKey =
  | "runDbId"
  | "vmLink"
  | "payloadBytes"
  | "pubRateHz"
  | "estUploadMbps"
  | "estDownloadMbps"
  | "meanRttMs"
  | "p95Ms"
  | "p99Ms"
  | "packetLossPercent";

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

function avg(values: number[]): number | undefined {
  if (values.length === 0) {
    return undefined;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function formatStat(value?: number, digits = 3, unit = "ms"): string {
  if (value == null || !Number.isFinite(value)) {
    return "-";
  }
  return `${value.toFixed(digits)}${unit ? ` ${unit}` : ""}`;
}

function formatOneDigit(value?: number): string {
  if (value == null || !Number.isFinite(value)) {
    return "-";
  }
  const text = value.toFixed(1);
  return text.endsWith(".0") ? text.slice(0, -2) : text;
}

function parseSizeToBytes(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
    return value;
  }
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

function parseRateHz(value: unknown): number | undefined {
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

function findRosParameters(value: unknown): JsonRecord | null {
  const root = asRecord(value);
  if (!root) {
    return null;
  }
  if (asRecord(root.ros__parameters)) {
    return asRecord(root.ros__parameters);
  }
  for (const nested of Object.values(root)) {
    const nestedRecord = asRecord(nested);
    if (!nestedRecord) {
      continue;
    }
    if (asRecord(nestedRecord.ros__parameters)) {
      return asRecord(nestedRecord.ros__parameters);
    }
  }
  return null;
}

function findRosParameterValue(value: unknown, key: string): unknown {
  const root = asRecord(value);
  if (!root) {
    return undefined;
  }
  const rootParams = asRecord(root.ros__parameters);
  if (rootParams && rootParams[key] != null) {
    return rootParams[key];
  }
  for (const nested of Object.values(root)) {
    const nestedRecord = asRecord(nested);
    if (!nestedRecord) {
      continue;
    }
    const params = asRecord(nestedRecord.ros__parameters);
    if (params && params[key] != null) {
      return params[key];
    }
  }
  return undefined;
}

function formatBytesAbbr(value?: number): string {
  if (value == null || !Number.isFinite(value)) {
    return "-";
  }
  if (value >= 1_000_000) {
    return `${formatOneDigit(value / 1_000_000)}MB`;
  }
  if (value >= 1_000) {
    return `${formatOneDigit(value / 1_000)}KB`;
  }
  return `${value.toFixed(0)}B`;
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

function extractVmLinkFromPhases(phasesValue: unknown): string {
  const phases = asRecordArray(phasesValue);
  let sender = "";
  let responder = "";
  const fallbackTargets: string[] = [];

  for (const phase of phases) {
    const actions = asRecordArray(phase.actions);
    for (const action of actions) {
      if (getString(action.type) !== "run_runtime_preset") {
        continue;
      }
      const target = getString(action.target);
      if (!target) {
        continue;
      }
      const preset = getString(action.preset).toLowerCase();
      if (!sender && preset.includes("sender")) {
        sender = target;
      }
      if (!responder && (preset.includes("responder") || preset.includes("receiver"))) {
        responder = target;
      }
      if (!fallbackTargets.includes(target)) {
        fallbackTargets.push(target);
      }
    }
  }

  if (sender && responder) {
    return `${sender} -> ${responder}`;
  }
  if (fallbackTargets.length >= 2) {
    return `${fallbackTargets[0]} -> ${fallbackTargets[1]}`;
  }
  return "";
}

function extractVmLinkFromRunDetail(runDetail: ProfilingGeneratedRun): string {
  const snapshotRuns = asRecord(asRecord(runDetail.rrt_config_execution_snapshot)?.runs);
  if (snapshotRuns) {
    for (const snapshotRun of Object.values(snapshotRuns)) {
      const snapshotRunRecord = asRecord(snapshotRun);
      if (!snapshotRunRecord) {
        continue;
      }
      const actions = asRecordArray(snapshotRunRecord.actions);
      let sender = "";
      let responder = "";
      for (const action of actions) {
        const target = getString(action.target);
        if (!target) {
          continue;
        }
        const preset = getString(action.preset).toLowerCase();
        if (!sender && preset.includes("sender")) {
          sender = target;
        }
        if (!responder && (preset.includes("responder") || preset.includes("receiver"))) {
          responder = target;
        }
      }
      if (sender && responder) {
        return `${sender} -> ${responder}`;
      }
    }
  }

  const resultRows = asRecordArray(runDetail.results);
  for (const resultRow of resultRows) {
    const link = extractVmLinkFromPhases(resultRow.phases);
    if (link) {
      return link;
    }
  }

  const trackerRuns = asRecord(asRecord(runDetail.phase_action_execution_status)?.runs);
  if (trackerRuns) {
    for (const trackerRun of Object.values(trackerRuns)) {
      const trackerRunRecord = asRecord(trackerRun);
      const link = extractVmLinkFromPhases(trackerRunRecord?.phases);
      if (link) {
        return link;
      }
    }
  }

  return "-";
}

function valueFromVariantValues(variantValues: JsonRecord, candidates: string[]): string {
  for (const key of candidates) {
    const value = getString(variantValues[key]);
    if (value) {
      return value;
    }
  }
  for (const [rawKey, rawValue] of Object.entries(variantValues)) {
    const suffix = rawKey.split(".").slice(-1)[0] ?? "";
    if (!candidates.includes(suffix)) {
      continue;
    }
    const text = getString(rawValue);
    if (text) {
      return text;
    }
  }
  return "";
}

function getRunSpecifics(run: ProfilingGeneratedRun): RunSpecificsView {
  const characterization = asRecord(run.characterization_parameters);
  const runMetadata = asRecord(run.run_metadata);
  const variantValues =
    asRecord(characterization?.variant_values) ?? asRecord(runMetadata?.variant_values);
  const edge = asRecord(characterization?.edge) ?? asRecord(runMetadata?.edge);

  const payload =
    getString(characterization?.payload_value) ||
    getString(runMetadata?.payload_value) ||
    (variantValues
      ? valueFromVariantValues(variantValues, ["payload_size", "payload", "payload_bytes", "message_size", "msg_size"])
      : "");
  const publishRate =
    getString(characterization?.publish_rate_value) ||
    getString(runMetadata?.publish_rate_value) ||
    (variantValues
      ? valueFromVariantValues(
          variantValues,
          [
            "frequency_hz",
            "publish_rate_hz",
            "publish_rate",
            "publish_frequency_hz",
            "publish_frequency",
            "frequency",
            "pub_rate_hz",
          ],
        )
      : "");
  const runtimeEnv = getString(characterization?.runtime_env_name) || getString(runMetadata?.runtime_env_name);
  const directLink = getString(characterization?.link);
  const edgeFrom = edge ? getString(edge.from) : "";
  const edgeTo = edge ? getString(edge.to) : "";
  const link = directLink || (edgeFrom && edgeTo ? `${edgeFrom} -> ${edgeTo}` : "");

  return {
    payload: payload || "-",
    publishRate: publishRate || "-",
    runtimeEnv: runtimeEnv || "-",
    link: link || "-",
  };
}

function extractTopologyNodes(detail: unknown): TopologyNodeView[] {
  const topology = asRecord(asRecord(detail)?.topology_overview);
  return asRecordArray(topology?.nodes).map((node) => {
    const running = Array.isArray(node.managed_containers_running)
      ? node.managed_containers_running.filter((item) => !!getString(item))
      : [];
    return {
      name: getString(node.name),
      reachability: getString(node.reachability).toUpperCase() || "UNKNOWN",
      containerRuntimeReady: Boolean(node.container_runtime_ready),
      managedContainersRunningCount: running.length,
    };
  });
}

function edgeLinkLabel(value: unknown, fallbackType: string): string {
  if (Array.isArray(value)) {
    const parts = value.map((item) => getString(item)).filter((item) => !!item);
    if (parts.length > 0) {
      return parts.join(", ");
    }
  }
  const text = getString(value);
  if (text) {
    return text;
  }
  return fallbackType || "-";
}

function extractTopologyEdges(detail: unknown): TopologyEdgeView[] {
  const topology = asRecord(asRecord(detail)?.topology_overview);
  return asRecordArray(topology?.edges).map((edge, index) => {
    const validation = asRecord(edge.validation);
    const metrics = asRecord(validation?.metrics);
    return {
      name: getString(edge.name) || `edge-${index + 1}`,
      from: getString(edge.from),
      to: getString(edge.to),
      link: edgeLinkLabel(edge.link, getString(edge.type)),
      validationStatus: getString(validation?.status).toUpperCase() || "UNKNOWN",
      checkedAt: getString(validation?.checked_at),
      pingLatencyMs: getNumber(metrics?.latency_avg_ms),
    };
  });
}

function formatEdgeCheckedSince(checkedAt: string): string {
  const text = formatRelativeSinceNow(checkedAt);
  if (!text) {
    return "";
  }
  return text.replace(/(\d+)\s+h\s+(\d+)\s+min/, "$1 h/$2 min");
}

function formatEdgePingSummary(edge: TopologyEdgeView): string {
  if (edge.pingLatencyMs == null || !Number.isFinite(edge.pingLatencyMs)) {
    return "ping: -";
  }
  const checkedSince = formatEdgeCheckedSince(edge.checkedAt);
  if (!checkedSince) {
    return `ping: ${formatOneDigit(edge.pingLatencyMs)} ms`;
  }
  return `ping: ${formatOneDigit(edge.pingLatencyMs)} ms (${checkedSince})`;
}

function humanizeKey(value: string): string {
  const normalized = value.replace(/[_.]/g, " ").replace(/\s+/g, " ").trim();
  if (!normalized) {
    return value;
  }
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function toDisplayValue(value: unknown): string {
  if (value == null) {
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "number" || typeof value === "string") {
    return String(value);
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return "[]";
    }
    const simple = value.every((item) => ["string", "number", "boolean"].includes(typeof item) || item == null);
    if (simple) {
      return value.map((item) => (item == null ? "null" : String(item))).join(", ");
    }
    return `${value.length} items`;
  }
  const record = asRecord(value);
  if (record) {
    return `${Object.keys(record).length} fields`;
  }
  return String(value);
}

function flattenRows(record: JsonRecord): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [];

  const walk = (value: unknown, prefix: string, depth: number): void => {
    if (!prefix) {
      return;
    }
    const nested = asRecord(value);
    if (nested && depth < 2) {
      Object.keys(nested)
        .sort()
        .forEach((key) => {
          walk(nested[key], `${prefix}.${key}`, depth + 1);
        });
      return;
    }
    rows.push({ label: humanizeKey(prefix), value: toDisplayValue(value) });
  };

  Object.keys(record)
    .sort()
    .forEach((key) => {
      walk(record[key], key, 0);
    });

  return rows;
}

function getActionTarget(actionValue: JsonRecord): string {
  const target = getString(actionValue.target);
  if (target) {
    return target;
  }
  if (Array.isArray(actionValue.targets)) {
    const list = actionValue.targets
      .map((item) => getString(item))
      .filter((item) => !!item)
      .join(", ");
    if (list) {
      return list;
    }
  }
  return "";
}

function buildActionView(actionValue: JsonRecord, index: number): ActionView {
  const details = Object.keys(actionValue)
    .filter((key) => !["type", "action", "target", "targets"].includes(key))
    .sort()
    .map((key) => ({
      label: humanizeKey(key),
      value: toDisplayValue(actionValue[key]),
    }));

  return {
    index: Number(actionValue.index ?? index + 1),
    type: getString(actionValue.type || actionValue.action) || "unknown",
    target: getActionTarget(actionValue),
    details,
  };
}

function buildPhaseView(phaseValue: JsonRecord, index: number): PhaseView {
  const details = Object.keys(phaseValue)
    .filter((key) => !["name", "mode", "actions", "useTemplate", "use_template"].includes(key))
    .sort()
    .map((key) => ({
      label: humanizeKey(key),
      value: toDisplayValue(phaseValue[key]),
    }));

  return {
    index: Number(phaseValue.index ?? index + 1),
    name: getString(phaseValue.name) || `phase-${index + 1}`,
    mode: getString(phaseValue.mode) || "sequential",
    template: getString(phaseValue.useTemplate || phaseValue.use_template),
    details,
    actions: asRecordArray(phaseValue.actions).map((actionValue, actionIndex) => buildActionView(actionValue, actionIndex)),
  };
}

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "220px 1fr" }, gap: 1, py: 0.3 }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}

export function ExperimentDetailPage() {
  const params = useParams<{ expName: string }>();
  const expName = params.expName ?? "";

  const detail = useExperimentStore((state) => state.experimentDetailByName[expName]);
  const loading = useExperimentStore((state) => state.loading);
  const error = useExperimentStore((state) => state.error);
  const fetchExperimentDetail = useExperimentStore((state) => state.fetchExperimentDetail);
  const fetchGeneratedRuns = useExperimentStore((state) => state.fetchGeneratedRuns);
  const fetchGeneratedRunDetail = useExperimentStore((state) => state.fetchGeneratedRunDetail);
  const generateExperiment = useExperimentStore((state) => state.generateExperiment);
  const startExperiment = useExperimentStore((state) => state.startExperiment);
  const deleteRunsByGeneration = useExperimentStore((state) => state.deleteRunsByGeneration);
  const generatedRuns = useExperimentStore((state) => state.generatedRuns);
  const generatedRunDetailById = useExperimentStore((state) => state.generatedRunDetailById);
  const selectedRunIds = useRunComparisonStore((state) => state.selectedRunIds);
  const toggleRunInComparison = useRunComparisonStore((state) => state.toggleRun);
  const [loadingRunCurves, setLoadingRunCurves] = useState(false);
  const [runCurveError, setRunCurveError] = useState("");
  const [runCurves, setRunCurves] = useState<RttCurveLine[]>([]);
  const [loadingRunStats, setLoadingRunStats] = useState(false);
  const [runStatsError, setRunStatsError] = useState("");
  const [runStatsRows, setRunStatsRows] = useState<RunComparisonStatsRow[]>([]);
  const [statsSortKey, setStatsSortKey] = useState<StatsSortKey>("runDbId");
  const [statsSortDirection, setStatsSortDirection] = useState<"asc" | "desc">("asc");
  const [actionLoading, setActionLoading] = useState<"" | "generate" | "start" | "delete">("");
  const [confirmGenerateOpen, setConfirmGenerateOpen] = useState(false);
  const [confirmStartOpen, setConfirmStartOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [actionResult, setActionResult] = useState<{ severity: "success" | "error"; message: string } | null>(null);
  const [selectedGenerationForRuns, setSelectedGenerationForRuns] = useState<number | "all">("all");
  const [selectedGenerationForCurves, setSelectedGenerationForCurves] = useState<number | "all">("all");
  const [selectedGenerationForStart, setSelectedGenerationForStart] = useState<number>(0);
  const [selectedGenerationForDelete, setSelectedGenerationForDelete] = useState<number>(0);
  const [selectedReplaceGeneration, setSelectedReplaceGeneration] = useState<number | null>(null);

  useEffect(() => {
    if (expName) {
      void fetchExperimentDetail(expName);
      void fetchGeneratedRuns({ expName });
    }
  }, [expName, fetchExperimentDetail, fetchGeneratedRuns]);

  const allRelatedRuns = useMemo(
    () =>
      detail
        ? generatedRuns
            .filter(
              (run) =>
                (run.experiment_name ?? "").trim() === detail.name ||
                (run.generated_plan_name ?? "").trim() === detail.name,
            )
            .sort((a, b) => {
              const left = new Date(b.created_at ?? "").getTime();
              const right = new Date(a.created_at ?? "").getTime();
              return left - right;
            })
        : [],
    [detail, generatedRuns],
  );
  const relatedRuns = useMemo(
    () =>
      selectedGenerationForRuns === "all"
        ? allRelatedRuns
        : allRelatedRuns.filter((run) => Number(run.gen_version ?? 0) === selectedGenerationForRuns),
    [allRelatedRuns, selectedGenerationForRuns],
  );
  const replaceableGenerations = useMemo<ReplaceableGenerationOption[]>(
    () => {
      const generationByVersion = new Map<number, ReplaceableGenerationOption>();
      allRelatedRuns.forEach((run) => {
        const genVersion = Number(run.gen_version ?? 0);
        if (!Number.isFinite(genVersion) || genVersion <= 0) {
          return;
        }
        const status = (run.status ?? "").toUpperCase();
        const existing = generationByVersion.get(genVersion) ?? {
          genVersion,
          total: 0,
          pendingCount: 0,
          runningCount: 0,
          succeededCount: 0,
          failedCount: 0,
          selectable: true,
        };
        existing.total += 1;
        if (status === "PENDING") {
          existing.pendingCount += 1;
        } else if (status === "RUNNING") {
          existing.runningCount += 1;
        } else if (status === "SUCCEEDED") {
          existing.succeededCount += 1;
        } else if (status === "FAILED") {
          existing.failedCount += 1;
        }
        generationByVersion.set(genVersion, existing);
      });

      const options = [...generationByVersion.values()]
        .map((item) => ({
          ...item,
          selectable: item.runningCount === 0 && item.total > 0,
        }))
        .sort((left, right) => right.genVersion - left.genVersion);
      return options;
    },
    [allRelatedRuns],
  );
  const generationSummary = useMemo<GenerationSummaryRow[]>(() => {
    const executionStatus = asRecord(detail?.execution_status);
    const rows = Array.isArray(executionStatus?.by_generation) ? executionStatus.by_generation : [];
    const normalized: GenerationSummaryRow[] = [];
    rows.forEach((item) => {
      const row = asRecord(item);
      if (!row) {
        return;
      }
      const genVersion = getNumber(row.gen_version);
      if (!genVersion || genVersion <= 0) {
        return;
      }
      normalized.push({
        genVersion,
        total: getNumber(row.total) ?? 0,
        pending: getNumber(row.pending) ?? 0,
        running: getNumber(row.running) ?? 0,
        succeeded: getNumber(row.succeeded) ?? 0,
        failed: getNumber(row.failed) ?? 0,
      });
    });
    return normalized.sort((left, right) => right.genVersion - left.genVersion);
  }, [detail]);
  const availableGenerations = useMemo<number[]>(() => {
    const values = new Set<number>();
    generationSummary.forEach((row) => values.add(row.genVersion));
    allRelatedRuns.forEach((run) => {
      const version = Number(run.gen_version ?? 0);
      if (Number.isFinite(version) && version > 0) {
        values.add(version);
      }
    });
    const current = Number(detail?.current_gen_version ?? 0);
    if (Number.isFinite(current) && current > 0) {
      values.add(current);
    }
    return [...values].sort((left, right) => right - left);
  }, [allRelatedRuns, detail?.current_gen_version, generationSummary]);
  useEffect(() => {
    const preferred = Number(detail?.current_gen_version ?? 0);
    if (preferred > 0 && availableGenerations.includes(preferred)) {
      if (selectedGenerationForStart !== preferred) {
        setSelectedGenerationForStart(preferred);
      }
    } else {
      const fallback = availableGenerations[0] ?? 0;
      if (selectedGenerationForStart !== fallback) {
        setSelectedGenerationForStart(fallback);
      }
    }

    if (
      selectedGenerationForRuns !== "all" &&
      !availableGenerations.includes(selectedGenerationForRuns)
    ) {
      setSelectedGenerationForRuns(availableGenerations[0] ?? "all");
    }
    if (
      selectedGenerationForCurves !== "all" &&
      !availableGenerations.includes(selectedGenerationForCurves)
    ) {
      setSelectedGenerationForCurves(availableGenerations[0] ?? "all");
    }
    const deleteFallback = availableGenerations[0] ?? 0;
    if (!availableGenerations.includes(selectedGenerationForDelete)) {
      setSelectedGenerationForDelete(deleteFallback);
    }
  }, [
    availableGenerations,
    detail?.current_gen_version,
    selectedGenerationForCurves,
    selectedGenerationForRuns,
    selectedGenerationForStart,
    selectedGenerationForDelete,
  ]);

  const curveRelatedRuns = useMemo(
    () =>
      selectedGenerationForCurves === "all"
        ? allRelatedRuns
        : allRelatedRuns.filter((run) => Number(run.gen_version ?? 0) === selectedGenerationForCurves),
    [allRelatedRuns, selectedGenerationForCurves],
  );
  const successfulRunIds = useMemo(
    () => curveRelatedRuns.filter((run) => (run.status ?? "").toUpperCase() === "SUCCEEDED").map((run) => run.id),
    [curveRelatedRuns],
  );
  const successfulRunDetails = useMemo(
    () =>
      successfulRunIds
        .map((id) => generatedRunDetailById[id])
        .filter((runDetail): runDetail is NonNullable<typeof runDetail> => Boolean(runDetail)),
    [generatedRunDetailById, successfulRunIds],
  );
  const topologyNodes = useMemo(() => extractTopologyNodes(detail), [detail]);
  const topologyEdges = useMemo(() => extractTopologyEdges(detail), [detail]);

  useEffect(() => {
    if (successfulRunIds.length === 0) {
      return;
    }
    const missingRunIds = successfulRunIds.filter((runId) => !generatedRunDetailById[runId]);
    if (missingRunIds.length === 0) {
      return;
    }
    void Promise.all(missingRunIds.map((runId) => fetchGeneratedRunDetail(runId)));
  }, [fetchGeneratedRunDetail, generatedRunDetailById, successfulRunIds]);

  useEffect(() => {
    if (!detail || successfulRunIds.length === 0) {
      setRunCurves([]);
      setRunCurveError("");
      setLoadingRunCurves(false);
      return;
    }

    if (successfulRunDetails.length === 0) {
      return;
    }

    const controller = new AbortController();
    const load = async (): Promise<void> => {
      setLoadingRunCurves(true);
      setRunCurveError("");
      const nextCurves: RttCurveLine[] = [];
      try {
        for (const runDetail of successfulRunDetails) {
          const perRunCurves = await loadCurvesFromExperimentRun(runDetail, controller.signal);
          perRunCurves.forEach((curve) => {
            nextCurves.push({
              key: `${runDetail.id}-${curve.runId}`,
              runNumber: runDetail.id,
              label: `Run #${runDetail.id} / ${curve.runId} | payload=${curve.payload} | freq=${curve.frequency}`,
              frequencyHz: curve.frequencyHz,
              points: curve.points,
            });
          });
        }
        if (controller.signal.aborted) {
          return;
        }
        setRunCurves(nextCurves);
        if (nextCurves.length === 0) {
          setRunCurveError("No RTT curves were found for successful runs in this experiment.");
        }
      } catch (loadError) {
        if (controller.signal.aborted) {
          return;
        }
        setRunCurves([]);
        setRunCurveError(`Failed to load RTT curves: ${String(loadError)}`);
      } finally {
        if (!controller.signal.aborted) {
          setLoadingRunCurves(false);
        }
      }
    };
    void load();

    return () => controller.abort();
  }, [detail, successfulRunDetails, successfulRunIds]);

  useEffect(() => {
    if (!detail || successfulRunIds.length === 0) {
      setRunStatsRows([]);
      setRunStatsError("");
      setLoadingRunStats(false);
      return;
    }
    if (successfulRunDetails.length === 0) {
      return;
    }

    const controller = new AbortController();
    const load = async (): Promise<void> => {
      setLoadingRunStats(true);
      setRunStatsError("");
      try {
        const rows = await Promise.all(
          successfulRunDetails.map(async (runDetail) => {
            const summaries = asRecordArray(runDetail.rrt_summaries).map((item) => {
              const summary = asRecord(item.rrt_summary_json);
              const latency = asRecord(summary?.latency_ms);
              return {
                mean: getNumber(latency?.avg),
                p95: getNumber(latency?.p95),
                p99: getNumber(latency?.p99),
                loss: getNumber(summary?.loss_percent),
              };
            });
            const configRows = asRecordArray(runDetail.rrt_configs).map((item) => {
              const params = findRosParameters(item.rrt_config_json);
              const payloadBytes =
                parseSizeToBytes(params?.payload_size) ??
                parseSizeToBytes(findRosParameterValue(item.rrt_config_json, "payload_size"));
              const responsePayloadBytes = parseSizeToBytes(
                findRosParameterValue(item.rrt_config_json, "response_payload_size"),
              );
              const pubRateHz =
                parseRateHz(params?.frequency_hz) ??
                parseRateHz(params?.publish_frequency_hz) ??
                parseRateHz(params?.publish_frequency) ??
                parseRateHz(params?.frequency) ??
                parseRateHz(findRosParameterValue(item.rrt_config_json, "frequency_hz"));
              return {
                payloadBytes,
                responsePayloadBytes,
                pubRateHz,
              };
            });
            const means = summaries.map((item) => item.mean).filter((v): v is number => v != null);
            const p95s = summaries.map((item) => item.p95).filter((v): v is number => v != null);
            const p99s = summaries.map((item) => item.p99).filter((v): v is number => v != null);
            const losses = summaries.map((item) => item.loss).filter((v): v is number => v != null);
            const payloads = configRows.map((item) => item.payloadBytes).filter((v): v is number => v != null);
            const responses = configRows.map((item) => item.responsePayloadBytes).filter((v): v is number => v != null);
            const pubRates = configRows.map((item) => item.pubRateHz).filter((v): v is number => v != null);
            const payloadBytes = avg(payloads);
            const responsePayloadBytes = avg(responses);
            const pubRateHz = avg(pubRates);
            return {
              runDbId: runDetail.id,
              vmLink: extractVmLinkFromRunDetail(runDetail),
              payloadBytes,
              pubRateHz,
              estUploadMbps: estimateBandwidthMbps(payloadBytes, pubRateHz),
              estDownloadMbps: estimateBandwidthMbps(responsePayloadBytes, pubRateHz),
              meanRttMs: avg(means),
              p95Ms: avg(p95s),
              p99Ms: avg(p99s),
              packetLossPercent: avg(losses),
            } as RunComparisonStatsRow;
          }),
        );
        if (controller.signal.aborted) {
          return;
        }
        setRunStatsRows(rows.sort((a, b) => a.runDbId - b.runDbId));
        if (rows.every((row) => row.meanRttMs == null && row.p95Ms == null && row.p99Ms == null && row.packetLossPercent == null)) {
          setRunStatsError("RTT summary statistics are not available yet for successful runs.");
        }
      } catch (loadError) {
        if (controller.signal.aborted) {
          return;
        }
        setRunStatsRows([]);
        setRunStatsError(`Failed to load run comparison statistics: ${String(loadError)}`);
      } finally {
        if (!controller.signal.aborted) {
          setLoadingRunStats(false);
        }
      }
    };
    void load();

    return () => controller.abort();
  }, [detail, successfulRunDetails, successfulRunIds]);

  const sortedRunStatsRows = useMemo(() => {
    const factor = statsSortDirection === "asc" ? 1 : -1;
    const valueFor = (row: RunComparisonStatsRow): number | string => {
      const value = row[statsSortKey];
      if (typeof value === "number") {
        return value;
      }
      if (typeof value === "string") {
        return value.toLowerCase();
      }
      return Number.POSITIVE_INFINITY;
    };
    return [...runStatsRows].sort((left, right) => {
      const leftValue = valueFor(left);
      const rightValue = valueFor(right);
      if (typeof leftValue === "string" || typeof rightValue === "string") {
        return String(leftValue).localeCompare(String(rightValue)) * factor;
      }
      return (leftValue - rightValue) * factor;
    });
  }, [runStatsRows, statsSortDirection, statsSortKey]);

  if (!expName) {
    return <EmptyState label="Missing experiment name." />;
  }

  if (loading && !detail) {
    return <LoadingState label="Loading experiment detail..." />;
  }
  if (error && !detail) {
    return <ErrorState message={error} />;
  }
  if (!detail) {
    return <EmptyState label={`Experiment ${expName} was not found.`} />;
  }

  const handleGenerate = async (replaceGeneration?: number): Promise<void> => {
    setActionResult(null);
    setActionLoading("generate");
    try {
      const payload = await generateExperiment(expName, { replaceGeneration });
      await Promise.all([fetchExperimentDetail(expName), fetchGeneratedRuns({ expName })]);
      const message = getString(payload.message) || `Generated experiment ${expName}.`;
      setActionResult({ severity: "success", message });
    } catch (actionError) {
      setActionResult({ severity: "error", message: toErrorMessage(actionError) });
    } finally {
      setActionLoading("");
    }
  };

  const handleStart = async (): Promise<void> => {
    setActionResult(null);
    setActionLoading("start");
    try {
      if (!selectedGenerationForStart || selectedGenerationForStart <= 0) {
        setActionResult({
          severity: "error",
          message: "Choose a generation before starting a run.",
        });
        return;
      }
      const payload = await startExperiment(expName, { genVersion: selectedGenerationForStart });
      await Promise.all([fetchExperimentDetail(expName), fetchGeneratedRuns({ expName })]);
      const message = getString(payload.message) || `Started experiment ${expName}.`;
      setActionResult({ severity: "success", message });
    } catch (actionError) {
      setActionResult({ severity: "error", message: toErrorMessage(actionError) });
    } finally {
      setActionLoading("");
    }
  };

  const handleDeleteRunsByGeneration = async (): Promise<void> => {
    if (!selectedGenerationForDelete || selectedGenerationForDelete <= 0) {
      setActionResult({ severity: "error", message: "Choose a generation to delete." });
      return;
    }
    setActionResult(null);
    setActionLoading("delete");
    try {
      const payload = await deleteRunsByGeneration(expName, { genVersion: selectedGenerationForDelete });
      await Promise.all([fetchExperimentDetail(expName), fetchGeneratedRuns({ expName })]);
      const message =
        getString(payload.message) || `Deleted runs for generation v${selectedGenerationForDelete}.`;
      setActionResult({ severity: "success", message });
    } catch (actionError) {
      setActionResult({ severity: "error", message: toErrorMessage(actionError) });
    } finally {
      setActionLoading("");
    }
  };

  const onGenerateClick = (): void => {
    setSelectedReplaceGeneration(null);
    setConfirmGenerateOpen(true);
  };
  const onStartClick = (): void => {
    const preferred = Number(detail.current_gen_version ?? 0);
    if (preferred > 0 && availableGenerations.includes(preferred)) {
      setSelectedGenerationForStart(preferred);
    } else {
      setSelectedGenerationForStart(availableGenerations[0] ?? 0);
    }
    setConfirmStartOpen(true);
  };
  const onDeleteClick = (): void => {
    const preferred = Number(detail.current_gen_version ?? 0);
    if (preferred > 0 && availableGenerations.includes(preferred)) {
      setSelectedGenerationForDelete(preferred);
    } else {
      setSelectedGenerationForDelete(availableGenerations[0] ?? 0);
    }
    setConfirmDeleteOpen(true);
  };
  const selectedStartGenerationSummary = generationSummary.find(
    (item) => item.genVersion === selectedGenerationForStart,
  );

  const executionRows = flattenRows(asRecord(detail.execution) ?? {});
  const selectionRows = flattenRows(asRecord(detail.selections) ?? {});
  const phaseViews = asRecordArray(detail.phases).map((phaseValue, index) => buildPhaseView(phaseValue, index));

  const onSortStats = (key: StatsSortKey): void => {
    if (statsSortKey === key) {
      setStatsSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
      return;
    }
    setStatsSortKey(key);
    setStatsSortDirection("asc");
  };

  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} spacing={1}>
        <Typography variant="h5">Experiment Detail</Typography>
        <Stack direction="row" spacing={1}>
          <TextField
            select
            size="small"
            label="Run Filter"
            value={selectedGenerationForRuns === "all" ? "all" : String(selectedGenerationForRuns)}
            onChange={(event) => {
              const value = event.target.value;
              setSelectedGenerationForRuns(value === "all" ? "all" : Number(value || 0));
            }}
            sx={{ minWidth: 120 }}
          >
            <MenuItem value="all">All</MenuItem>
            {availableGenerations.map((version) => (
              <MenuItem key={`filter-gen-${version}`} value={version}>
                v{version}
              </MenuItem>
            ))}
          </TextField>
          <Button
            variant="contained"
            size="small"
            onClick={onGenerateClick}
            disabled={!!actionLoading}
          >
            {actionLoading === "generate" ? "Generating..." : "Generate Experiment"}
          </Button>
          <Button
            variant="contained"
            size="small"
            color="warning"
            onClick={onStartClick}
            disabled={!!actionLoading || !detail.has_generated_plan || !selectedGenerationForStart}
          >
            {actionLoading === "start" ? "Starting..." : "Start Run"}
          </Button>
          <Button component={Link} to="/experiment" variant="outlined" size="small">
            Back to list
          </Button>
        </Stack>
      </Stack>
      {error && <ErrorState message={error} />}
      {actionResult ? <Alert severity={actionResult.severity}>{actionResult.message}</Alert> : null}
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6">{detail.name}</Typography>
        <Typography color="text.secondary" sx={{ mb: 1 }}>
          {detail.description ?? "No description"}
        </Typography>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
          <Typography variant="body2">Status:</Typography>
          <StatusBadge value={detail.status} />
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2">Scenario:</Typography>
          {detail.scenario_name ? (
            <Button
              component={Link}
              to={`/scenario/${encodeURIComponent(detail.scenario_name)}`}
              variant="outlined"
              size="small"
            >
              {detail.scenario_name}
            </Button>
          ) : (
            <Typography variant="body2">-</Typography>
          )}
        </Stack>
        <Typography variant="body2">Start Count: {detail.start_count ?? 0}</Typography>
        <Typography variant="body2">Last Started: {formatDateTime(detail.last_started_at)}</Typography>
        <Typography variant="body2">Generated Plan: {detail.has_generated_plan ? "Yes" : "No"}</Typography>
        <Typography variant="body2">Current Generation: v{detail.current_gen_version ?? 0}</Typography>
        {generationSummary.length > 0 ? (
          <TableContainer sx={{ mt: 1.2 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Generation</TableCell>
                  <TableCell align="right">Total</TableCell>
                  <TableCell align="right">Pending</TableCell>
                  <TableCell align="right">Running</TableCell>
                  <TableCell align="right">Succeeded</TableCell>
                  <TableCell align="right">Failed</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {generationSummary.map((row) => (
                  <TableRow key={`gen-summary-${row.genVersion}`}>
                    <TableCell>v{row.genVersion}</TableCell>
                    <TableCell align="right">{row.total}</TableCell>
                    <TableCell align="right">{row.pending}</TableCell>
                    <TableCell align="right">{row.running}</TableCell>
                    <TableCell align="right">{row.succeeded}</TableCell>
                    <TableCell align="right">{row.failed}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        ) : null}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Nodes & Links
        </Typography>
        {topologyNodes.length === 0 && topologyEdges.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No scenario node/link data available.
          </Typography>
        ) : (
          <Stack spacing={1}>
            <Typography variant="subtitle2">Nodes</Typography>
            {topologyNodes.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No nodes found.
              </Typography>
            ) : (
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", md: "repeat(2, minmax(0, 1fr))" },
                  gap: 1,
                }}
              >
                {topologyNodes.map((node) => (
                  <Paper key={`node-${node.name}`} variant="outlined" sx={{ p: 1 }}>
                    <Stack spacing={0.8}>
                      <Stack direction="row" justifyContent="space-between" alignItems="center">
                        <Typography
                          variant="body2"
                          component={Link}
                          to={`/inventory/${encodeURIComponent(node.name)}`}
                          sx={{
                            textDecoration: "none",
                            color: "text.primary",
                            fontWeight: 600,
                            "&:hover": { textDecoration: "underline" },
                          }}
                        >
                          {node.name}
                        </Typography>
                        <StatusBadge value={node.reachability} />
                      </Stack>
                      <Typography variant="caption" color="text.secondary">
                        Container Runtime: {node.containerRuntimeReady ? "READY" : "NOT READY"}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        Running Containers: {node.managedContainersRunningCount}
                      </Typography>
                    </Stack>
                  </Paper>
                ))}
              </Box>
            )}
            <Typography variant="subtitle2" sx={{ mt: 0.8 }}>
              Links
            </Typography>
            {topologyEdges.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No selected edges found in this experiment YAML.
              </Typography>
            ) : (
              <Stack spacing={0.5}>
                {topologyEdges.map((edge) => (
                  <Paper key={`edge-${edge.name}`} variant="outlined" sx={{ p: 0.8 }}>
                    <Stack
                      direction={{ xs: "column", md: "row" }}
                      spacing={1}
                      alignItems={{ xs: "flex-start", md: "center" }}
                    >
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {edge.from} {"->"} {edge.to}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {edge.link}
                      </Typography>
                      <StatusBadge value={edge.validationStatus} />
                      <Typography variant="caption" color="text.secondary">
                        {formatEdgePingSummary(edge)}
                      </Typography>
                    </Stack>
                  </Paper>
                ))}
              </Stack>
            )}
          </Stack>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Experiment Runs
        </Typography>
        {relatedRuns.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No runs found for this experiment.
          </Typography>
        ) : (
          <Stack spacing={0.8}>
            {relatedRuns.map((run) => {
              const specifics = getRunSpecifics(run);
              return (
                <Paper key={run.id} variant="outlined" sx={{ p: 1 }}>
                  <Stack
                    direction={{ xs: "column", sm: "row" }}
                    spacing={1}
                    justifyContent="space-between"
                    alignItems={{ xs: "flex-start", sm: "center" }}
                  >
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        Run #{run.id}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {run.gen_version ? `v${run.gen_version}` : "-"}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {run.plan_run_id ? `(${run.plan_run_id})` : ""}
                      </Typography>
                      <StatusBadge value={run.status} />
                    </Stack>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography variant="body2" color="text.secondary">
                        {formatDateTime(run.created_at)}
                      </Typography>
                      <Button
                        variant={selectedRunIds.includes(run.id) ? "contained" : "outlined"}
                        size="small"
                        startIcon={<CompareArrowsIcon fontSize="small" />}
                        onClick={() => toggleRunInComparison(run.id)}
                      >
                        {selectedRunIds.includes(run.id) ? "In Comparison" : "Compare"}
                      </Button>
                      <Button component={Link} to={`/experiment/runs/${run.id}`} variant="outlined" size="small">
                        Open
                      </Button>
                    </Stack>
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    {`Payload: ${specifics.payload} | Publish Rate: ${specifics.publishRate} | Runtime: ${specifics.runtimeEnv} | Link: ${specifics.link}`}
                  </Typography>
                </Paper>
              );
            })}
          </Stack>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          RTT Curves (Successful Runs)
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ mb: 1 }} alignItems={{ sm: "center" }}>
          <TextField
            select
            size="small"
            label="Curve Version"
            value={selectedGenerationForCurves === "all" ? "all" : String(selectedGenerationForCurves)}
            onChange={(event) => {
              const value = event.target.value;
              setSelectedGenerationForCurves(value === "all" ? "all" : Number(value || 0));
            }}
            sx={{ minWidth: 140 }}
          >
            <MenuItem value="all">All</MenuItem>
            {availableGenerations.map((version) => (
              <MenuItem key={`curve-filter-gen-${version}`} value={version}>
                v{version}
              </MenuItem>
            ))}
          </TextField>
          <Typography variant="body2" color="text.secondary">
            Showing: {selectedGenerationForCurves === "all" ? "all generations" : `v${selectedGenerationForCurves}`}
          </Typography>
        </Stack>
        {successfulRunIds.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No successful runs are available for curve visualization.
          </Typography>
        ) : (
          <Stack spacing={1.2}>
            {loadingRunCurves ? <LoadingState label="Loading successful run curve data..." /> : null}
            {runCurveError ? <ErrorState message={runCurveError} /> : null}
            {!loadingRunCurves && runCurves.length > 0 ? (
              <RttCurveOverlay curves={runCurves} buildRunLink={(runId) => `/experiment/runs/${runId}`} />
            ) : null}
            <Typography variant="subtitle2" sx={{ mt: 1 }}>
              Run Comparison Statistics
            </Typography>
            {loadingRunStats ? <LoadingState label="Loading RTT summary statistics..." /> : null}
            {runStatsError ? <ErrorState message={runStatsError} /> : null}
            {!loadingRunStats && runStatsRows.length > 0 ? (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell sortDirection={statsSortKey === "runDbId" ? statsSortDirection : false}>
                        <TableSortLabel
                          active={statsSortKey === "runDbId"}
                          direction={statsSortKey === "runDbId" ? statsSortDirection : "asc"}
                          onClick={() => onSortStats("runDbId")}
                        >
                          Run
                        </TableSortLabel>
                      </TableCell>
                      <TableCell sortDirection={statsSortKey === "vmLink" ? statsSortDirection : false}>
                        <TableSortLabel
                          active={statsSortKey === "vmLink"}
                          direction={statsSortKey === "vmLink" ? statsSortDirection : "asc"}
                          onClick={() => onSortStats("vmLink")}
                        >
                          Link
                        </TableSortLabel>
                      </TableCell>
                      <TableCell align="right" sortDirection={statsSortKey === "payloadBytes" ? statsSortDirection : false}>
                        <TableSortLabel
                          active={statsSortKey === "payloadBytes"}
                          direction={statsSortKey === "payloadBytes" ? statsSortDirection : "asc"}
                          onClick={() => onSortStats("payloadBytes")}
                        >
                          Payload
                        </TableSortLabel>
                      </TableCell>
                      <TableCell align="right" sortDirection={statsSortKey === "pubRateHz" ? statsSortDirection : false}>
                        <TableSortLabel
                          active={statsSortKey === "pubRateHz"}
                          direction={statsSortKey === "pubRateHz" ? statsSortDirection : "asc"}
                          onClick={() => onSortStats("pubRateHz")}
                        >
                          Pub_Rate
                        </TableSortLabel>
                      </TableCell>
                      <TableCell align="right" sortDirection={statsSortKey === "estUploadMbps" ? statsSortDirection : false}>
                        <TableSortLabel
                          active={statsSortKey === "estUploadMbps"}
                          direction={statsSortKey === "estUploadMbps" ? statsSortDirection : "asc"}
                          onClick={() => onSortStats("estUploadMbps")}
                        >
                          Up BW
                        </TableSortLabel>
                      </TableCell>
                      <TableCell align="right" sortDirection={statsSortKey === "estDownloadMbps" ? statsSortDirection : false}>
                        <TableSortLabel
                          active={statsSortKey === "estDownloadMbps"}
                          direction={statsSortKey === "estDownloadMbps" ? statsSortDirection : "asc"}
                          onClick={() => onSortStats("estDownloadMbps")}
                        >
                          Down BW
                        </TableSortLabel>
                      </TableCell>
                      <TableCell align="right" sortDirection={statsSortKey === "meanRttMs" ? statsSortDirection : false}>
                        <TableSortLabel
                          active={statsSortKey === "meanRttMs"}
                          direction={statsSortKey === "meanRttMs" ? statsSortDirection : "asc"}
                          onClick={() => onSortStats("meanRttMs")}
                        >
                          Mean RTT
                        </TableSortLabel>
                      </TableCell>
                      <TableCell align="right" sortDirection={statsSortKey === "p95Ms" ? statsSortDirection : false}>
                        <TableSortLabel
                          active={statsSortKey === "p95Ms"}
                          direction={statsSortKey === "p95Ms" ? statsSortDirection : "asc"}
                          onClick={() => onSortStats("p95Ms")}
                        >
                          P95
                        </TableSortLabel>
                      </TableCell>
                      <TableCell align="right" sortDirection={statsSortKey === "p99Ms" ? statsSortDirection : false}>
                        <TableSortLabel
                          active={statsSortKey === "p99Ms"}
                          direction={statsSortKey === "p99Ms" ? statsSortDirection : "asc"}
                          onClick={() => onSortStats("p99Ms")}
                        >
                          P99
                        </TableSortLabel>
                      </TableCell>
                      <TableCell align="right" sortDirection={statsSortKey === "packetLossPercent" ? statsSortDirection : false}>
                        <TableSortLabel
                          active={statsSortKey === "packetLossPercent"}
                          direction={statsSortKey === "packetLossPercent" ? statsSortDirection : "asc"}
                          onClick={() => onSortStats("packetLossPercent")}
                        >
                          Packet Loss
                        </TableSortLabel>
                      </TableCell>
                      <TableCell align="center">Compare</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {sortedRunStatsRows.map((row) => (
                      <TableRow key={`stats-${row.runDbId}`}>
                        <TableCell>
                          <Button
                            component={Link}
                            to={`/experiment/runs/${row.runDbId}`}
                            variant="text"
                            size="small"
                            sx={{ minWidth: 0, p: 0, textTransform: "none" }}
                          >
                            {`Run #${row.runDbId}`}
                          </Button>
                        </TableCell>
                        <TableCell>
                          {(() => {
                            const parts = row.vmLink
                              .split("->")
                              .map((item) => item.trim())
                              .filter((item) => !!item);
                            if (parts.length === 2) {
                              const [fromVm, toVm] = parts;
                              return (
                                <>
                                  <Button
                                    component={Link}
                                    to={`/inventory/${encodeURIComponent(fromVm)}`}
                                    variant="text"
                                    size="small"
                                    sx={{ minWidth: 0, p: 0 }}
                                  >
                                    {fromVm}
                                  </Button>
                                  {" -> "}
                                  <Button
                                    component={Link}
                                    to={`/inventory/${encodeURIComponent(toVm)}`}
                                    variant="text"
                                    size="small"
                                    sx={{ minWidth: 0, p: 0 }}
                                  >
                                    {toVm}
                                  </Button>
                                </>
                              );
                            }
                            if (parts.length === 1 && parts[0] !== "-") {
                              const vmName = parts[0];
                              return (
                                <Button
                                  component={Link}
                                  to={`/inventory/${encodeURIComponent(vmName)}`}
                                  variant="text"
                                  size="small"
                                  sx={{ minWidth: 0, p: 0 }}
                                >
                                  {vmName}
                                </Button>
                              );
                            }
                            return row.vmLink;
                          })()}
                        </TableCell>
                        <TableCell align="right">{formatBytesAbbr(row.payloadBytes)}</TableCell>
                        <TableCell align="right">
                          {row.pubRateHz == null || !Number.isFinite(row.pubRateHz) ? "-" : `${formatOneDigit(row.pubRateHz)}Hz`}
                        </TableCell>
                        <TableCell align="right">
                          {row.estUploadMbps == null || !Number.isFinite(row.estUploadMbps)
                            ? "-"
                            : `${formatOneDigit(row.estUploadMbps)}Mbps`}
                        </TableCell>
                        <TableCell align="right">
                          {row.estDownloadMbps == null || !Number.isFinite(row.estDownloadMbps)
                            ? "-"
                            : `${formatOneDigit(row.estDownloadMbps)}Mbps`}
                        </TableCell>
                        <TableCell align="right">
                          {row.meanRttMs == null || !Number.isFinite(row.meanRttMs)
                            ? "-"
                            : `${formatOneDigit(row.meanRttMs)} ms`}
                        </TableCell>
                        <TableCell align="right">
                          {row.p95Ms == null || !Number.isFinite(row.p95Ms) ? "-" : `${formatOneDigit(row.p95Ms)} ms`}
                        </TableCell>
                        <TableCell align="right">
                          {row.p99Ms == null || !Number.isFinite(row.p99Ms) ? "-" : `${formatOneDigit(row.p99Ms)} ms`}
                        </TableCell>
                        <TableCell align="right">{formatStat(row.packetLossPercent, 3, "%")}</TableCell>
                        <TableCell align="center">
                          <Button
                            variant={selectedRunIds.includes(row.runDbId) ? "contained" : "outlined"}
                            size="small"
                            startIcon={<CompareArrowsIcon fontSize="small" />}
                            onClick={() => toggleRunInComparison(row.runDbId)}
                          >
                            {selectedRunIds.includes(row.runDbId) ? "In Comparison" : "Compare"}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            ) : null}
          </Stack>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Execution
        </Typography>
        {executionRows.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No execution settings are defined.
          </Typography>
        ) : (
          <Stack spacing={0.25}>
            {executionRows.map((row) => (
              <DetailRow key={`execution-${row.label}`} label={row.label} value={row.value} />
            ))}
          </Stack>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Selections
        </Typography>
        {selectionRows.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No selections are defined.
          </Typography>
        ) : (
          <Stack spacing={0.25}>
            {selectionRows.map((row) => (
              <DetailRow key={`selection-${row.label}`} label={row.label} value={row.value} />
            ))}
          </Stack>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Phases
        </Typography>
        {phaseViews.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No phases are defined.
          </Typography>
        ) : (
          <Stack spacing={1}>
            {phaseViews.map((phaseValue) => (
              <Accordion defaultExpanded key={`${phaseValue.index}-${phaseValue.name}`} disableGutters>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Stack
                    direction={{ xs: "column", sm: "row" }}
                    spacing={1.2}
                    alignItems={{ xs: "flex-start", sm: "center" }}
                    sx={{ width: "100%", pr: 1 }}
                  >
                    <Typography variant="subtitle1" sx={{ minWidth: 180, fontWeight: 600 }}>
                      {phaseValue.name}
                    </Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                      Mode: {phaseValue.mode}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {phaseValue.actions.length} actions
                    </Typography>
                  </Stack>
                </AccordionSummary>
                <AccordionDetails>
                  <Stack spacing={1}>
                    <DetailRow label="Mode" value={phaseValue.mode} />
                    <DetailRow label="Template" value={phaseValue.template || "-"} />
                    {phaseValue.details.length > 0 ? (
                      <Stack spacing={0.2}>
                        {phaseValue.details.map((row) => (
                          <DetailRow key={`${phaseValue.name}-${row.label}`} label={row.label} value={row.value} />
                        ))}
                      </Stack>
                    ) : null}

                    {phaseValue.actions.length === 0 ? (
                      <Typography variant="body2" color="text.secondary">
                        No actions are defined for this phase.
                      </Typography>
                    ) : (
                      phaseValue.actions.map((actionValue) => (
                        <Paper key={`${phaseValue.name}-${actionValue.index}`} variant="outlined" sx={{ p: 1.1 }}>
                          <Stack
                            direction={{ xs: "column", sm: "row" }}
                            spacing={1}
                            alignItems={{ xs: "flex-start", sm: "center" }}
                            justifyContent="space-between"
                          >
                            <Typography variant="subtitle2">
                              Action {actionValue.index}: {actionValue.type}
                            </Typography>
                            <Typography variant="body2" color="text.secondary">
                              Target: {actionValue.target || "-"}
                            </Typography>
                          </Stack>
                          {actionValue.details.length > 0 ? (
                            <Stack spacing={0.2} sx={{ mt: 0.8 }}>
                              {actionValue.details.map((row) => (
                                <DetailRow
                                  key={`${phaseValue.name}-${actionValue.index}-${row.label}`}
                                  label={row.label}
                                  value={row.value}
                                />
                              ))}
                            </Stack>
                          ) : null}
                        </Paper>
                      ))
                    )}
                  </Stack>
                </AccordionDetails>
              </Accordion>
            ))}
          </Stack>
        )}
      </Paper>
      <Stack direction="row" justifyContent="flex-end">
        <Button
          variant="outlined"
          color="error"
          size="small"
          disabled={!!actionLoading || availableGenerations.length === 0}
          onClick={onDeleteClick}
        >
          {actionLoading === "delete" ? "Deleting..." : "Delete Runs by Version"}
        </Button>
      </Stack>
      <Dialog open={confirmGenerateOpen} onClose={() => setConfirmGenerateOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Regenerate Experiment?</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ mt: 0.5 }}>
            <DialogContentText>
              {`This experiment has ${successfulRunIds.length} successful run(s). Generate a full pending set, or choose one pending generation to replace.`}
            </DialogContentText>
            <TextField
              select
              size="small"
              label="Replace Pending Generation (Optional)"
              value={selectedReplaceGeneration == null ? "" : String(selectedReplaceGeneration)}
              onChange={(event) => {
                const next = Number(event.target.value || 0);
                setSelectedReplaceGeneration(next > 0 ? next : null);
              }}
              fullWidth
            >
              <MenuItem value="">None (generate full set)</MenuItem>
              {replaceableGenerations.map((option) => (
                <MenuItem
                  key={`replace-gen-${option.genVersion}`}
                  value={option.genVersion}
                  disabled={!option.selectable}
                >
                  {`v${option.genVersion} (total=${option.total}, pending=${option.pendingCount}, running=${option.runningCount}, succeeded=${option.succeededCount}, failed=${option.failedCount})`}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmGenerateOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() => {
              setConfirmGenerateOpen(false);
              void handleGenerate(
                typeof selectedReplaceGeneration === "number" && selectedReplaceGeneration > 0
                  ? selectedReplaceGeneration
                  : undefined,
              );
            }}
          >
            Generate
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog open={confirmDeleteOpen} onClose={() => setConfirmDeleteOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Delete Runs by Version</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ mt: 0.5 }}>
            <DialogContentText>
              Select one generation version. All runs in that version will be deleted, except runs currently RUNNING.
            </DialogContentText>
            <TextField
              select
              size="small"
              label="Generation Version"
              value={selectedGenerationForDelete > 0 ? String(selectedGenerationForDelete) : ""}
              onChange={(event) => setSelectedGenerationForDelete(Number(event.target.value || 0))}
              fullWidth
            >
              {availableGenerations.length === 0 ? (
                <MenuItem value="" disabled>
                  No generation
                </MenuItem>
              ) : null}
              {availableGenerations.map((version) => (
                <MenuItem key={`delete-gen-${version}`} value={version}>
                  v{version}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDeleteOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            color="error"
            disabled={actionLoading === "delete" || !selectedGenerationForDelete}
            onClick={() => {
              setConfirmDeleteOpen(false);
              void handleDeleteRunsByGeneration();
            }}
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog open={confirmStartOpen} onClose={() => setConfirmStartOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Start Experiment Run</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ mt: 0.5 }}>
            <DialogContentText>
              Choose the generation to start. The default is the latest generation.
            </DialogContentText>
            <TextField
              select
              size="small"
              label="Generation"
              value={selectedGenerationForStart > 0 ? String(selectedGenerationForStart) : ""}
              onChange={(event) => setSelectedGenerationForStart(Number(event.target.value || 0))}
              fullWidth
            >
              {availableGenerations.length === 0 ? (
                <MenuItem value="" disabled>
                  No generation
                </MenuItem>
              ) : null}
              {availableGenerations.map((version) => (
                <MenuItem key={`start-dialog-gen-${version}`} value={version}>
                  v{version}
                </MenuItem>
              ))}
            </TextField>
            {selectedStartGenerationSummary ? (
              <DialogContentText>
                {`v${selectedStartGenerationSummary.genVersion}: pending=${selectedStartGenerationSummary.pending}, running=${selectedStartGenerationSummary.running}, succeeded=${selectedStartGenerationSummary.succeeded}, failed=${selectedStartGenerationSummary.failed}`}
              </DialogContentText>
            ) : null}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmStartOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            color="warning"
            disabled={actionLoading === "start" || !selectedGenerationForStart}
            onClick={() => {
              setConfirmStartOpen(false);
              void handleStart();
            }}
          >
            Start
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
