import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  CircularProgress,
  Paper,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import { type ReactNode, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { SyntaxHighlighter, oneDark } from "../../../shared/components/SyntaxHighlighter";

import { endpoints } from "../../../core/api/endpoints";
import { toErrorMessage } from "../../../core/api/errors";
import { requestJson } from "../../../core/api/http";
import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { StatusBadge } from "../../../shared/components/StatusBadge";
import { formatDateTime } from "../../../shared/utils/date";
import { useAuthStore } from "../../../stores/auth.store";
import { useScenarioStore } from "../../../stores/scenario.store";
import { ScenarioGraphView } from "../components/ScenarioGraphView";
import type { Scenario } from "../types";

type JsonRecord = Record<string, unknown>;

interface SnapshotActionView {
  index: number;
  type: string;
  target: string;
  details: Array<{ label: string; value: string }>;
}

interface SnapshotPhaseView {
  index: number;
  name: string;
  mode: string;
  template: string;
  details: Array<{ label: string; value: string }>;
  actions: SnapshotActionView[];
}

interface SnapshotPayloadView {
  index: number;
  name: string;
  payloadType: string;
  details: Array<{ label: string; value: string }>;
}

interface SnapshotEdgeView {
  index: number;
  name: string;
  source: string;
  target: string;
  linkType: string;
  edgeType: string;
}

interface EdgeValidationSummary {
  status: string;
  message: string;
  latencyAvgMs: number | null;
}

interface PhaseValidationSummary {
  status: string;
  validatedAt: string;
  taskId: string;
  runId: string;
  resultMessage: string;
}

interface SetupRoutingScript {
  target: string;
  script: string;
}

interface ValidatePhaseResponse {
  scenario?: string;
  phase_name?: string | null;
  validation_status?: string;
  run_id?: number;
  task_id?: string;
  execution_mode?: string;
}

type ConnectivityValidationMode = "nodes" | "graph";
type ValidationMessageSeverity = "info" | "success" | "warning" | "error";

interface ValidateConnectivityResponse {
  scenario?: string;
  validation_status?: string;
  execution_mode?: string;
  run_id?: number;
  edge_name?: string;
  task_id?: string;
  task_state?: string;
  queue_error?: string;
  graph_check_summary?: Record<string, unknown>;
  result?: Record<string, unknown>;
  progress?: Record<string, unknown>;
}

interface ValidationMessage {
  severity: ValidationMessageSeverity;
  lines: string[];
}

async function copyPlainText(text: string): Promise<boolean> {
  const payload = String(text ?? "");
  if (!payload) {
    return false;
  }
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(payload);
      return true;
    } catch {
      // fall through to legacy copy path
    }
  }
  if (typeof document === "undefined") {
    return false;
  }
  const textarea = document.createElement("textarea");
  textarea.value = payload;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  document.body.removeChild(textarea);
  return copied;
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

function getNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => getString(item)).filter((item) => Boolean(item));
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

function omitKeys(record: JsonRecord, keys: string[]): JsonRecord {
  const output: JsonRecord = {};
  Object.keys(record).forEach((key) => {
    if (!keys.includes(key)) {
      output[key] = record[key];
    }
  });
  return output;
}

function getActionTarget(actionValue: JsonRecord): string {
  const target = getString(actionValue.target);
  if (target) {
    return target;
  }
  if (Array.isArray(actionValue.targets)) {
    const joined = actionValue.targets
      .map((item) => getString(item))
      .filter((item) => !!item)
      .join(", ");
    if (joined) {
      return joined;
    }
  }
  return "";
}

function buildActionView(actionValue: JsonRecord, index: number): SnapshotActionView {
  return {
    index: Number(actionValue.index ?? index + 1),
    type: getString(actionValue.type || actionValue.action) || "unknown",
    target: getActionTarget(actionValue),
    details: flattenRows(omitKeys(actionValue, ["index", "type", "action", "target", "targets"])),
  };
}

function buildPhaseView(phaseValue: JsonRecord, index: number): SnapshotPhaseView {
  const actions = asRecordArray(phaseValue.actions);
  const steps = asRecordArray(phaseValue.steps);
  const actionSource = actions.length > 0 ? actions : steps;

  return {
    index: Number(phaseValue.index ?? index + 1),
    name: getString(phaseValue.name) || `phase-${index + 1}`,
    mode: getString(phaseValue.mode) || "sequential",
    template: getString(phaseValue.useTemplate || phaseValue.use_template || phaseValue.source_template),
    details: flattenRows(
      omitKeys(phaseValue, ["index", "name", "mode", "actions", "steps", "useTemplate", "use_template", "source_template"]),
    ),
    actions: actionSource.map((actionValue, actionIndex) => buildActionView(actionValue, actionIndex)),
  };
}

function buildPayloadView(payloadValue: JsonRecord, index: number): SnapshotPayloadView {
  return {
    index: index + 1,
    name: getString(payloadValue.name) || `payload-${index + 1}`,
    payloadType: getString(payloadValue.type || payloadValue.kind) || "-",
    details: flattenRows(omitKeys(payloadValue, ["name", "type", "kind"])),
  };
}

function edgeLinkType(edgeValue: JsonRecord): string {
  const rawLink = edgeValue.link;
  if (typeof rawLink === "string") {
    return rawLink.trim();
  }
  if (Array.isArray(rawLink)) {
    return rawLink
      .map((item) => getString(item))
      .filter((item) => Boolean(item))
      .join("/");
  }
  return "";
}

function buildEdgeView(edgeValue: JsonRecord, index: number): SnapshotEdgeView | null {
  const source = getString(edgeValue.from);
  const target = getString(edgeValue.to);
  if (!source || !target) {
    return null;
  }
  return {
    index: index + 1,
    name: getString(edgeValue.name) || `edge-${index + 1}`,
    source,
    target,
    linkType: edgeLinkType(edgeValue),
    edgeType: getString(edgeValue.type),
  };
}

function normalizeCheckStatus(value: unknown): string {
  if (typeof value === "boolean") {
    return value ? "PASS" : "FAIL";
  }
  const text = getString(value).toUpperCase();
  if (!text) {
    return "UNKNOWN";
  }
  if (["PASS", "FAIL", "PENDING", "RUNNING", "SKIPPED", "SUCCEEDED", "FAILED", "IDLE"].includes(text)) {
    return text;
  }
  return "UNKNOWN";
}

function toBadgeDisplayStatus(value: unknown, role: "all" | "node" | "graph" | "phase"): string {
  const normalized = normalizeCheckStatus(value);
  if (normalized === "UNKNOWN") {
    return "NOT CHECKED";
  }
  if (normalized === "IDLE") {
    return role === "all" ? "PARTIAL" : "NOT CHECKED";
  }
  if (normalized === "PASS" || normalized === "SUCCEEDED" || normalized === "SUCCESS") {
    return "SUCCESS";
  }
  if (normalized === "FAIL" || normalized === "FAILED" || normalized === "ERROR") {
    return "FAILED";
  }
  return normalized;
}

function findCheckPayload(response: ValidateConnectivityResponse, key: "nodes" | "graph"): JsonRecord | null {
  const resultChecks = asRecord(asRecord(response.result)?.checks);
  const progressChecks = asRecord(asRecord(response.progress)?.checks);
  return asRecord(resultChecks?.[key]) ?? asRecord(progressChecks?.[key]);
}

function firstFailureMessage(rows: JsonRecord[], label: "node" | "edge"): string {
  const failed = rows.find((row) => row.ok === false);
  if (!failed) {
    return "";
  }
  const name = getString(failed.node || failed.edge_name || failed.source_node) || label;
  const reason = getString(failed.error || failed.message || failed.stderr) || "validation failed";
  return `${humanizeKey(label)} '${name}': ${reason}`;
}

function buildConnectivityValidationMessage(
  mode: ConnectivityValidationMode,
  response: ValidateConnectivityResponse,
): ValidationMessage {
  const lines: string[] = [];
  const modeLabel = mode === "nodes" ? "Node reachability" : "Edge connectivity";
  const executionMode = getString(response.execution_mode) || "queued";
  const runId = typeof response.run_id === "number" ? String(response.run_id) : "";
  const taskId = getString(response.task_id);
  const taskState = getString(response.task_state);
  const queueError = getString(response.queue_error);

  lines.push(`${modeLabel} validation requested (${executionMode}).`);
  if (runId) {
    lines.push(`Run: ${runId}`);
  }
  if (taskId) {
    lines.push(`Task: ${taskId}`);
  }
  if (taskState) {
    lines.push(`Task State: ${taskState}`);
  }

  if (mode === "nodes") {
    const nodeCheck = findCheckPayload(response, "nodes");
    const nodeResults = asRecordArray(nodeCheck?.results);
    if (nodeResults.length > 0) {
      const passed = nodeResults.filter((row) => row.ok === true).length;
      const failed = nodeResults.filter((row) => row.ok === false).length;
      lines.push(`Nodes: ${passed}/${nodeResults.length} reachable`);
      if (failed > 0) {
        const failure = firstFailureMessage(nodeResults, "node");
        if (failure) {
          lines.push(failure);
        }
      }
    }
  }

  if (mode === "graph") {
    const summary = asRecord(response.graph_check_summary);
    if (summary) {
      const total = getNumber(summary.total_edges);
      const checked = getNumber(summary.checked_edges);
      const passed = getNumber(summary.passed_edges);
      const failed = getNumber(summary.failed_edges);
      const summaryParts = [
        total != null ? `total=${total}` : "",
        checked != null ? `checked=${checked}` : "",
        passed != null ? `pass=${passed}` : "",
        failed != null ? `fail=${failed}` : "",
      ].filter((part) => Boolean(part));
      if (summaryParts.length > 0) {
        lines.push(`Graph Summary: ${summaryParts.join(", ")}`);
      }
    }

    const graphCheck = findCheckPayload(response, "graph");
    const graphResults = asRecordArray(graphCheck?.results);
    if (graphResults.length > 0) {
      const failure = firstFailureMessage(graphResults, "edge");
      if (failure) {
        lines.push(failure);
      }
    } else {
      const summaryResults = asRecordArray(summary?.results);
      const failure = firstFailureMessage(summaryResults, "edge");
      if (failure) {
        lines.push(failure);
      }
    }
  }

  if (queueError) {
    lines.push(`Queue error: ${queueError}`);
    return { severity: "warning", lines };
  }

  if (lines.some((line) => line.toLowerCase().includes("failed") || line.toLowerCase().includes("error"))) {
    return { severity: "warning", lines };
  }

  return { severity: "info", lines };
}

function actionStatusFromHistoryEntry(entry: JsonRecord): string {
  const entries = asRecordArray(entry.entries);
  for (const row of entries) {
    const actionName = getString(row.action_name).toLowerCase();
    if (actionName === "actions") {
      return normalizeCheckStatus(row.status);
    }
  }
  return "UNKNOWN";
}

function phaseStateKey(phaseName: string, edgeName?: string): string {
  return edgeName ? `${edgeName}::${phaseName}` : phaseName;
}

function latestPhaseHistoryEntry(detail: Scenario, phaseName: string): JsonRecord | null {
  const history = Array.isArray(detail.validation_history) ? detail.validation_history : [];
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const item = asRecord(history[index]);
    if (!item) {
      continue;
    }
    if (getString(item.phase_name) !== phaseName) {
      continue;
    }
    return item;
  }
  return null;
}

function latestPhaseResultMessage(detail: Scenario, phaseName: string, edgeName?: string): string {
  const payload = asRecord(detail.last_validation_data);
  if (!payload) {
    return "";
  }
  if (getString(payload.phase_name) !== phaseName) {
    return "";
  }
  if (edgeName && getString(payload.edge_name) !== edgeName) {
    return "";
  }

  const failureReport = asRecord(payload.failure_report);
  const failureSummary = getString(failureReport?.summary);
  if (failureSummary) {
    return failureSummary;
  }

  const checks = asRecord(payload.checks);
  const actions = asRecord(checks?.actions);
  const graph = asRecord(checks?.graph);
  const nodes = asRecord(checks?.nodes);

  const actionErrors = asStringArray(actions?.errors);
  if (actionErrors.length > 0) {
    return actionErrors[0];
  }
  const graphError = getString(graph?.error);
  if (graphError) {
    return graphError;
  }
  const nodeErrors = asStringArray(nodes?.errors);
  if (nodeErrors.length > 0) {
    return nodeErrors[0];
  }

  const execution = asRecord(actions?.execution);
  const actionResults = asRecordArray(execution?.results);
  const failedAction = actionResults.find((item) => item.success === false || item.ok === false);
  if (failedAction) {
    return (
      getString(failedAction.message || failedAction.error) ||
      `Action '${getString(failedAction.type) || "unknown"}' failed.`
    );
  }

  const graphResults = asRecordArray(graph?.results);
  const failedGraph = graphResults.find((item) => item.ok === false);
  if (failedGraph) {
    return (
      getString(failedGraph.message || failedGraph.error) ||
      `Ping from '${getString(failedGraph.source_node)}' to '${getString(failedGraph.target_node)}' failed.`
    );
  }

  if (payload.ok === true) {
    return "Validation completed successfully.";
  }
  return "";
}

function fallbackResultMessage(status: string): string {
  const normalized = normalizeCheckStatus(status);
  if (normalized === "PASS" || normalized === "SUCCEEDED") {
    return "Validation completed successfully.";
  }
  if (normalized === "FAIL" || normalized === "FAILED") {
    return "Validation failed.";
  }
  if (normalized === "RUNNING" || normalized === "PENDING") {
    return "Validation in progress.";
  }
  return "-";
}

function latestPhaseValidationSummary(detail: Scenario, phaseName: string, edgeName?: string): PhaseValidationSummary | null {
  const historyEntry = latestPhaseHistoryEntry(detail, phaseName);
  const fallbackRunId = getString(historyEntry?.run);
  const fallbackTaskId = getString(historyEntry?.task_id);
  const fallbackValidatedAt = getString(historyEntry?.validated_at);
  const resultMessage = latestPhaseResultMessage(detail, phaseName, edgeName);

  if (edgeName) {
    const lastData = asRecord(detail.last_validation_data);
    if (!lastData) {
      return null;
    }
    if (getString(lastData.phase_name) !== phaseName || getString(lastData.edge_name) !== edgeName) {
      return null;
    }
    const checks = asRecord(lastData.checks);
    const actions = asRecord(checks?.actions);
    return {
      status: normalizeCheckStatus(actions?.ok ?? detail.check_status_actions),
      validatedAt: getString(detail.last_validation_at) || fallbackValidatedAt,
      taskId: getString(lastData.task_id) || getString(detail.last_validation_task_id) || fallbackTaskId,
      runId: getString(lastData.run_id) || fallbackRunId,
      resultMessage,
    };
  }
  if (historyEntry) {
    return {
      status: actionStatusFromHistoryEntry(historyEntry),
      validatedAt: fallbackValidatedAt,
      taskId: fallbackTaskId,
      runId: fallbackRunId,
      resultMessage,
    };
  }

  const lastData = asRecord(detail.last_validation_data);
  if (!lastData || getString(lastData.phase_name) !== phaseName) {
    return null;
  }
  const checks = asRecord(lastData.checks);
  const actions = asRecord(checks?.actions);
  return {
    status: normalizeCheckStatus(actions?.ok ?? detail.check_status_actions),
    validatedAt: getString(detail.last_validation_at) || fallbackValidatedAt,
    taskId: getString(lastData.task_id) || getString(detail.last_validation_task_id) || fallbackTaskId,
    runId: getString(lastData.run_id) || fallbackRunId,
    resultMessage,
  };
}

function graphSummaryFromDetail(detail: Scenario): JsonRecord {
  return asRecord(detail.last_graph_check_summary) ?? asRecord(asRecord(detail.last_validation_data)?.graph_check_summary) ?? {};
}

function latestEdgeValidationSummary(detail: Scenario, edgeName: string): EdgeValidationSummary | null {
  const summary = graphSummaryFromDetail(detail);
  const rows = asRecordArray(summary.results);
  const row = rows.find((item) => getString(item.edge_name) === edgeName);
  if (!row) {
    return null;
  }
  const metrics = asRecord(row.metrics);
  return {
    status: normalizeCheckStatus(row.ok),
    message: getString(row.message || row.error),
    latencyAvgMs: getNumber(metrics?.latency_avg_ms),
  };
}

function buildSetupRoutingScripts(detail: Scenario, edgeName: string): SetupRoutingScript[] {
  const lastData = asRecord(detail.last_validation_data);
  if (!lastData) {
    return [];
  }
  if (getString(lastData.phase_name) !== "setup_routing") {
    return [];
  }
  if (getString(lastData.edge_name) !== edgeName) {
    return [];
  }

  const checks = asRecord(lastData.checks);
  const actions = asRecord(checks?.actions);
  const execution = asRecord(actions?.execution);
  const rows = asRecordArray(execution?.results);
  const commandByTarget = new Map<string, string[]>();

  for (const row of rows) {
    if (getString(row.type) !== "run_runtime_preset") {
      continue;
    }
    const target = getString(row.target);
    if (!target) {
      continue;
    }
    const debug = asRecord(row.debug);
    const command =
      getString(debug?.command_final) || getString(debug?.command_rendered) || getString(debug?.command);
    if (!command) {
      continue;
    }
    const existing = commandByTarget.get(target) ?? [];
    existing.push(command);
    commandByTarget.set(target, existing);
  }

  return Array.from(commandByTarget.entries()).map(([target, commands]) => ({
    target,
    script: [
      "#!/usr/bin/env bash",
      "set -euo pipefail",
      "",
      `# phase: setup_routing`,
      `# edge: ${edgeName}`,
      `# target: ${target}`,
      "",
      commands.join("\n\n"),
      "",
    ].join("\n"),
  }));
}

function extractPresetFromAction(action: SnapshotActionView): string {
  for (const row of action.details) {
    if (row.label.toLowerCase().includes("preset")) {
      const value = getString(row.value);
      if (value && value !== "-") {
        return value;
      }
    }
  }
  return "";
}

function extractActionDetailValue(action: SnapshotActionView, matcher: (label: string) => boolean): string {
  for (const row of action.details) {
    const label = row.label.toLowerCase();
    if (matcher(label)) {
      const value = getString(row.value);
      if (value && value !== "-") {
        return value;
      }
    }
  }
  return "";
}

function resolveTargetRef(rawTargetRef: string, selectedEdge: SnapshotEdgeView | null): string {
  const targetRef = rawTargetRef.trim();
  if (!targetRef) {
    return "";
  }
  const normalized = targetRef.startsWith("vm:") ? targetRef.slice(3) : targetRef;
  if (normalized === "${edge.to}") {
    return selectedEdge?.target ?? normalized;
  }
  if (normalized === "${edge.from}") {
    return selectedEdge?.source ?? normalized;
  }
  return normalized;
}

function plannedTargetForAction(action: SnapshotActionView, selectedEdge: SnapshotEdgeView | null): string {
  if (action.target) {
    return action.target;
  }
  const targetRef = extractActionDetailValue(
    action,
    (label) => label.includes("targetref") || label.includes("target ref"),
  );
  if (targetRef) {
    return resolveTargetRef(targetRef, selectedEdge);
  }
  return "";
}

function buildSetupRoutingScriptsForPhase(
  detail: Scenario,
  edgeName: string,
  actions: SnapshotActionView[],
  selectedEdge: SnapshotEdgeView | null,
): SetupRoutingScript[] {
  const fromExecution = buildSetupRoutingScripts(detail, edgeName);
  const commandsByTarget = new Map<string, string[]>();
  for (const item of fromExecution) {
    const existing = commandsByTarget.get(item.target) ?? [];
    existing.push(item.script);
    commandsByTarget.set(item.target, existing);
  }

  const plannedRoutingActions = actions
    .filter((item) => item.type === "run_runtime_preset")
    .map((item) => ({ action: item, resolvedTarget: plannedTargetForAction(item, selectedEdge) }))
    .filter((item) => Boolean(item.resolvedTarget));
  const plannedTargets: string[] = [];
  for (const entry of plannedRoutingActions) {
    if (!plannedTargets.includes(entry.resolvedTarget)) {
      plannedTargets.push(entry.resolvedTarget);
    }
  }

  if (plannedTargets.length === 0) {
    return fromExecution;
  }

  return plannedTargets.map((target) => {
    const scripts = commandsByTarget.get(target) ?? [];
    if (scripts.length > 0) {
      return {
        target,
        script: scripts.join("\n\n"),
      };
    }

    const plannedForTarget = plannedRoutingActions.filter((item) => item.resolvedTarget === target);
    const planHints = plannedForTarget
      .map((entry) => {
        const preset = extractPresetFromAction(entry.action);
        return preset
          ? `# planned action[${entry.action.index}] preset=${preset}`
          : `# planned action[${entry.action.index}]`;
      })
      .join("\n");

    return {
      target,
      script: [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        `# phase: setup_routing`,
        `# edge: ${edgeName}`,
        `# target: ${target}`,
        planHints || "# planned action",
        "",
        "# No rendered command was captured for this target in the latest validation run.",
        "# The phase likely stopped before this action executed.",
        "",
      ].join("\n"),
    };
  });
}

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "220px 1fr" }, gap: 1, py: 0.25 }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}

export function ScenarioDetailPage() {
  const params = useParams<{ scenarioName: string }>();
  const scenarioName = params.scenarioName ?? "";

  const detail = useScenarioStore((state) => state.detailByName[scenarioName]);
  const loadingDetail = useScenarioStore((state) => state.loadingDetail);
  const error = useScenarioStore((state) => state.error);
  const fetchDetail = useScenarioStore((state) => state.fetchDetail);
  const token = useAuthStore((state) => state.token);
  const [phaseSubmitting, setPhaseSubmitting] = useState<Record<string, boolean>>({});
  const [phaseQuerying, setPhaseQuerying] = useState<Record<string, boolean>>({});
  const [phaseError, setPhaseError] = useState<Record<string, string>>({});
  const [phaseQueryError, setPhaseQueryError] = useState<Record<string, string>>({});
  const [phaseResponse, setPhaseResponse] = useState<Record<string, string>>({});
  const [phaseQueriedSummary, setPhaseQueriedSummary] = useState<Record<string, PhaseValidationSummary | null>>({});
  const [connectivitySubmittingMode, setConnectivitySubmittingMode] = useState<ConnectivityValidationMode | null>(null);
  const [connectivityMessage, setConnectivityMessage] = useState<ValidationMessage | null>(null);
  const [edgeSubmitting, setEdgeSubmitting] = useState<Record<string, boolean>>({});
  const [edgeQuerying, setEdgeQuerying] = useState<Record<string, boolean>>({});
  const [edgeResponse, setEdgeResponse] = useState<Record<string, string>>({});
  const [edgeError, setEdgeError] = useState<Record<string, string>>({});
  const [edgeQueryError, setEdgeQueryError] = useState<Record<string, string>>({});
  const [selectedEdgeTab, setSelectedEdgeTab] = useState(0);
  const [rawYamlCopyStatus, setRawYamlCopyStatus] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => {
    if (scenarioName) {
      void fetchDetail(scenarioName);
    }
  }, [fetchDetail, scenarioName]);

  useEffect(() => {
    if (!scenarioName || !detail) {
      return undefined;
    }
    const status = String(detail.validation_status ?? "").toUpperCase();
    if (!["PENDING", "RUNNING"].includes(status)) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void fetchDetail(scenarioName);
    }, 3000);
    return () => {
      window.clearInterval(timer);
    };
  }, [detail, fetchDetail, scenarioName]);

  useEffect(() => {
    setRawYamlCopyStatus("idle");
  }, [scenarioName, detail?.updated_at]);

  async function validatePhase(phaseName: string, edgeName?: string): Promise<void> {
    if (!scenarioName) {
      return;
    }
    const stateKey = phaseStateKey(phaseName, edgeName);
    if (!token) {
      setPhaseError((state) => ({ ...state, [stateKey]: "Missing access token. Please login again." }));
      return;
    }

    setPhaseSubmitting((state) => ({ ...state, [stateKey]: true }));
    setPhaseError((state) => ({ ...state, [stateKey]: "" }));
    setPhaseResponse((state) => ({ ...state, [stateKey]: "" }));
    try {
      const response = await requestJson<ValidatePhaseResponse>(endpoints.scenario.validate(scenarioName), {
        method: "POST",
        token,
        body: {
          check_actions: true,
          check_nodes: false,
          check_graph: Boolean(edgeName),
          phase_name: phaseName,
          edge_name: edgeName,
        },
      });
      const runId = typeof response.run_id === "number" ? `run#${response.run_id}` : "";
      const taskId = getString(response.task_id);
      const mode = getString(response.execution_mode) || "queued";
      const responseText = [mode, runId, taskId ? `task=${taskId}` : ""].filter((item) => !!item).join(" | ");
      setPhaseResponse((state) => ({
        ...state,
        [stateKey]: responseText || "Validation request submitted.",
      }));
      await fetchDetail(scenarioName);
    } catch (requestError) {
      setPhaseError((state) => ({ ...state, [stateKey]: toErrorMessage(requestError) }));
    } finally {
      setPhaseSubmitting((state) => ({ ...state, [stateKey]: false }));
    }
  }

  async function validateConnectivity(mode: ConnectivityValidationMode): Promise<void> {
    if (!scenarioName) {
      return;
    }
    if (!token) {
      setConnectivityMessage({ severity: "error", lines: ["Missing access token. Please login again."] });
      return;
    }

    const isNodeMode = mode === "nodes";
    setConnectivitySubmittingMode(mode);
    setConnectivityMessage(null);
    try {
      const response = await requestJson<ValidateConnectivityResponse>(endpoints.scenario.validate(scenarioName), {
        method: "POST",
        token,
        body: {
          check_actions: false,
          check_nodes: isNodeMode,
          check_graph: !isNodeMode,
        },
      });
      setConnectivityMessage(buildConnectivityValidationMessage(mode, response));
      await fetchDetail(scenarioName);
    } catch (requestError) {
      setConnectivityMessage({ severity: "error", lines: [toErrorMessage(requestError)] });
    } finally {
      setConnectivitySubmittingMode(null);
    }
  }

  async function queryPhaseResult(phaseName: string, edgeName?: string): Promise<void> {
    if (!scenarioName) {
      return;
    }
    const stateKey = phaseStateKey(phaseName, edgeName);
    if (!token) {
      setPhaseQueryError((state) => ({ ...state, [stateKey]: "Missing access token. Please login again." }));
      return;
    }

    setPhaseQuerying((state) => ({ ...state, [stateKey]: true }));
    setPhaseQueryError((state) => ({ ...state, [stateKey]: "" }));
    try {
      const latestDetail = await requestJson<Scenario>(endpoints.scenario.detail(scenarioName), { token });
      const summary = latestPhaseValidationSummary(latestDetail, phaseName, edgeName);
      setPhaseQueriedSummary((state) => ({ ...state, [stateKey]: summary }));
      setPhaseResponse((state) => ({
        ...state,
        [stateKey]: summary ? `Queried latest result for '${phaseName}'.` : `No validation result yet for '${phaseName}'.`,
      }));
    } catch (requestError) {
      setPhaseQueryError((state) => ({ ...state, [stateKey]: toErrorMessage(requestError) }));
    } finally {
      setPhaseQuerying((state) => ({ ...state, [stateKey]: false }));
    }
  }

  async function validateEdge(edgeName: string): Promise<void> {
    if (!scenarioName) {
      return;
    }
    if (!token) {
      setEdgeError((state) => ({ ...state, [edgeName]: "Missing access token. Please login again." }));
      return;
    }

    setEdgeSubmitting((state) => ({ ...state, [edgeName]: true }));
    setEdgeError((state) => ({ ...state, [edgeName]: "" }));
    setEdgeQueryError((state) => ({ ...state, [edgeName]: "" }));
    setEdgeResponse((state) => ({ ...state, [edgeName]: "" }));
    try {
      const response = await requestJson<ValidateConnectivityResponse>(endpoints.scenario.validate(scenarioName), {
        method: "POST",
        token,
        body: {
          check_actions: false,
          check_nodes: false,
          check_graph: true,
          edge_name: edgeName,
        },
      });
      const runId = typeof response.run_id === "number" ? `run#${response.run_id}` : "";
      const taskId = getString(response.task_id);
      const mode = getString(response.execution_mode) || "queued";
      const text = [
        `Graph validation for edge '${edgeName}' requested`,
        mode,
        runId,
        taskId ? `task=${taskId}` : "",
      ]
        .filter((item) => Boolean(item))
        .join(" | ");
      setEdgeResponse((state) => ({ ...state, [edgeName]: text }));
      await fetchDetail(scenarioName);
    } catch (requestError) {
      setEdgeError((state) => ({ ...state, [edgeName]: toErrorMessage(requestError) }));
    } finally {
      setEdgeSubmitting((state) => ({ ...state, [edgeName]: false }));
    }
  }

  async function queryEdgeResult(edgeName: string): Promise<void> {
    if (!scenarioName) {
      return;
    }
    if (!token) {
      setEdgeQueryError((state) => ({ ...state, [edgeName]: "Missing access token. Please login again." }));
      return;
    }

    setEdgeQuerying((state) => ({ ...state, [edgeName]: true }));
    setEdgeQueryError((state) => ({ ...state, [edgeName]: "" }));
    try {
      const latestDetail = await requestJson<Scenario>(endpoints.scenario.detail(scenarioName), { token });
      const edgeSummary = latestEdgeValidationSummary(latestDetail, edgeName);
      if (!edgeSummary) {
        setEdgeResponse((state) => ({ ...state, [edgeName]: `No validation result yet for edge '${edgeName}'.` }));
        return;
      }
      const latencyText = edgeSummary.latencyAvgMs != null ? ` | ${edgeSummary.latencyAvgMs.toFixed(1)} ms` : "";
      const reasonText = edgeSummary.message ? ` | ${edgeSummary.message}` : "";
      setEdgeResponse((state) => ({
        ...state,
        [edgeName]: `Edge '${edgeName}' status: ${edgeSummary.status}${latencyText}${reasonText}`,
      }));
    } catch (requestError) {
      setEdgeQueryError((state) => ({ ...state, [edgeName]: toErrorMessage(requestError) }));
    } finally {
      setEdgeQuerying((state) => ({ ...state, [edgeName]: false }));
    }
  }

  if (!scenarioName) {
    return <EmptyState label="Missing scenario name." />;
  }

  if (loadingDetail && !detail) {
    return <LoadingState label="Loading scenario detail..." />;
  }
  if (error && !detail) {
    return <ErrorState message={error} />;
  }
  if (!detail) {
    return <EmptyState label={`Scenario ${scenarioName} was not found.`} />;
  }

  const rawPayload = asRecord(detail.raw_payload) ?? {};
  const spec = asRecord(rawPayload.spec) ?? {};
  const phaseTemplates = asRecordArray(spec.phaseTemplates);
  const legacyPhases = asRecordArray(spec.phases);
  const phaseSource = phaseTemplates.length > 0 ? phaseTemplates : legacyPhases;
  const phaseSourceLabel = phaseTemplates.length > 0 ? "spec.phaseTemplates" : legacyPhases.length > 0 ? "spec.phases" : "-";

  const serializerPayloads = asRecordArray(detail.payloads);
  const specPayloads = asRecordArray(spec.payloads);
  const payloadSource = serializerPayloads.length > 0 ? serializerPayloads : specPayloads;
  const payloadSourceLabel = serializerPayloads.length > 0 ? "scenario.payloads" : specPayloads.length > 0 ? "spec.payloads" : "-";
  const serializerEdges = asRecordArray(detail.graph_edges);
  const specEdges = asRecordArray(asRecord(spec.graph)?.edges);
  const edgeSource = serializerEdges.length > 0 ? serializerEdges : specEdges;
  const edgeSourceLabel = serializerEdges.length > 0 ? "scenario.graph_edges" : specEdges.length > 0 ? "spec.graph.edges" : "-";

  const phaseViews = phaseSource.map((phaseValue, index) => buildPhaseView(phaseValue, index));
  const payloadViews = payloadSource.map((payloadValue, index) => buildPayloadView(payloadValue, index));
  const edgeViews = edgeSource
    .map((edgeValue, index) => buildEdgeView(edgeValue, index))
    .filter((edgeValue): edgeValue is SnapshotEdgeView => Boolean(edgeValue));
  const lastGraphSummary = asRecord(detail.last_graph_check_summary);
  const lastGraphResults = asRecordArray(lastGraphSummary?.results);
  const graphResultByEdgeName = new Map(
    lastGraphResults.map((item) => {
      return [getString(item.edge_name), item] as const;
    }),
  );
  const effectiveSelectedEdgeTab = selectedEdgeTab < edgeViews.length ? selectedEdgeTab : 0;
  const selectedEdge = edgeViews[effectiveSelectedEdgeTab] ?? null;
  const allStatus = toBadgeDisplayStatus(detail.validation_status, "all");
  const nodeSummary = asRecord(detail.last_node_check_summary);
  const nodeStatusSource =
    normalizeCheckStatus(detail.check_status_node) === "UNKNOWN" ? nodeSummary?.status : detail.check_status_node;
  const nodeStatus = toBadgeDisplayStatus(nodeStatusSource, "node");
  const graphStatus = toBadgeDisplayStatus(detail.check_status_graph, "graph");
  const phaseStatus = toBadgeDisplayStatus(detail.check_status_actions, "phase");
  const rawYaml = getString(detail.raw_yaml);

  async function handleCopyRawYaml(): Promise<void> {
    const ok = await copyPlainText(rawYaml);
    setRawYamlCopyStatus(ok ? "copied" : "failed");
  }

  return (
    <Stack spacing={2} sx={{ width: "100%", minWidth: 0 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", sm: "center" }}
        spacing={1}
      >
        <Typography variant="h5">Scenario Detail</Typography>
        <Button component={Link} to="/scenario" variant="outlined" size="small">
          Back to list
        </Button>
      </Stack>
      {error && <ErrorState message={error} />}
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6">{detail.name}</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {detail.description ?? "No description"}
        </Typography>
        <Stack direction="row" spacing={1.2} useFlexGap flexWrap="wrap">
          <Stack direction="row" spacing={0.6} alignItems="center">
            <Typography variant="body2" color="text.secondary">
              ALL:
            </Typography>
            <StatusBadge value={allStatus} />
          </Stack>
          <Stack direction="row" spacing={0.6} alignItems="center">
            <Typography variant="body2" color="text.secondary">
              Node:
            </Typography>
            <StatusBadge value={nodeStatus} />
          </Stack>
          <Stack direction="row" spacing={0.6} alignItems="center">
            <Typography variant="body2" color="text.secondary">
              Graph:
            </Typography>
            <StatusBadge value={graphStatus} />
          </Stack>
          <Stack direction="row" spacing={0.6} alignItems="center">
            <Typography variant="body2" color="text.secondary">
              Phase:
            </Typography>
            <StatusBadge value={phaseStatus} />
          </Stack>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Updated: {formatDateTime(detail.updated_at)}
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ mt: 1.5 }}>
          <Button
            size="small"
            variant="outlined"
            disabled={connectivitySubmittingMode !== null}
            onClick={() => void validateConnectivity("nodes")}
          >
            {connectivitySubmittingMode === "nodes" ? (
              <Stack direction="row" spacing={0.8} alignItems="center">
                <CircularProgress size={14} />
                <span>Validating Nodes...</span>
              </Stack>
            ) : (
              "Validate Nodes (-n)"
            )}
          </Button>
          <Button
            size="small"
            variant="outlined"
            disabled={connectivitySubmittingMode !== null}
            onClick={() => void validateConnectivity("graph")}
          >
            {connectivitySubmittingMode === "graph" ? (
              <Stack direction="row" spacing={0.8} alignItems="center">
                <CircularProgress size={14} />
                <span>Validating Graph...</span>
              </Stack>
            ) : (
              "Validate Graph (-g)"
            )}
          </Button>
        </Stack>
        {connectivityMessage ? (
          <Alert severity={connectivityMessage.severity} sx={{ mt: 1.2 }}>
            {connectivityMessage.lines.map((line, index) => (
              <Typography key={`connectivity-msg-${index}`} variant="body2">
                {line}
              </Typography>
            ))}
          </Alert>
        ) : null}
      </Paper>
      <ScenarioGraphView scenario={detail} />
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Phase / Payload Snapshot
        </Typography>
        <DetailRow label="Phase Source" value={phaseSourceLabel} />
        <DetailRow label="Payload Source" value={payloadSourceLabel} />
        <DetailRow label="Edge Source" value={edgeSourceLabel} />
        <DetailRow label="Phase Count" value={String(phaseViews.length)} />
        <DetailRow label="Payload Count" value={String(payloadViews.length)} />
        <DetailRow label="Edge Count" value={String(edgeViews.length)} />

        <Typography variant="subtitle1" sx={{ mt: 1.6 }}>
          Edge Snapshots
        </Typography>
        {edgeViews.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No graph edges were found in the scenario.
          </Typography>
        ) : (
          <Stack spacing={0.8} sx={{ mt: 0.6 }}>
            <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
              <Tabs
                value={effectiveSelectedEdgeTab}
                onChange={(_, newValue: number) => setSelectedEdgeTab(newValue)}
                variant="scrollable"
                scrollButtons="auto"
                allowScrollButtonsMobile
                aria-label="edge snapshots"
              >
                {edgeViews.map((edgeValue, index) => (
                  <Tab key={`edge-tab-${edgeValue.name}-${index}`} label={edgeValue.name} />
                ))}
              </Tabs>
            </Box>
            {selectedEdge ? (
              (() => {
                const edgeResult = graphResultByEdgeName.get(selectedEdge.name);
                const edgeMetrics = asRecord(edgeResult?.metrics);
                const edgeLatencyAvgMs = getNumber(edgeMetrics?.latency_avg_ms);
                const validating = Boolean(edgeSubmitting[selectedEdge.name]);
                const querying = Boolean(edgeQuerying[selectedEdge.name]);
                return (
                  <Paper variant="outlined" sx={{ p: 1.2 }}>
                    <Stack spacing={0.8}>
                      <Stack
                        direction={{ xs: "column", sm: "row" }}
                        spacing={1}
                        alignItems={{ xs: "flex-start", sm: "center" }}
                        sx={{ width: "100%", pr: 1 }}
                      >
                        <Typography variant="subtitle2" sx={{ minWidth: 220, fontWeight: 600 }}>
                          {selectedEdge.name}
                        </Typography>
                        <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                          {selectedEdge.source} -&gt; {selectedEdge.target}
                        </Typography>
                        {edgeResult ? (
                          <StatusBadge value={normalizeCheckStatus(edgeResult.ok)} />
                        ) : (
                          <Typography variant="body2" color="text.secondary">
                            No check result
                          </Typography>
                        )}
                        <Button
                          size="small"
                          variant="outlined"
                          disabled={validating}
                          onClick={() => {
                            void validateEdge(selectedEdge.name);
                          }}
                        >
                          {validating ? (
                            <Stack direction="row" spacing={0.8} alignItems="center">
                              <CircularProgress size={14} />
                              <span>Validating...</span>
                            </Stack>
                          ) : (
                            "Validate Edge"
                          )}
                        </Button>
                        <Button
                          size="small"
                          variant="text"
                          disabled={querying}
                          onClick={() => {
                            void queryEdgeResult(selectedEdge.name);
                          }}
                        >
                          {querying ? (
                            <Stack direction="row" spacing={0.8} alignItems="center">
                              <CircularProgress size={14} />
                              <span>Querying...</span>
                            </Stack>
                          ) : (
                            "Query Result"
                          )}
                        </Button>
                      </Stack>

                      {edgeResponse[selectedEdge.name] ? <Alert severity="info">{edgeResponse[selectedEdge.name]}</Alert> : null}
                      {edgeError[selectedEdge.name] ? <Alert severity="error">{edgeError[selectedEdge.name]}</Alert> : null}
                      {edgeQueryError[selectedEdge.name] ? <Alert severity="error">{edgeQueryError[selectedEdge.name]}</Alert> : null}
                      <DetailRow label="Link Type" value={selectedEdge.linkType || "-"} />
                      <DetailRow label="Edge Type" value={selectedEdge.edgeType || "-"} />
                      <DetailRow
                        label="Ping Latency Avg"
                        value={edgeLatencyAvgMs != null ? `${edgeLatencyAvgMs.toFixed(1)} ms` : "-"}
                      />
                      <DetailRow label="Last Checked" value={detail.last_validation_at ? formatDateTime(detail.last_validation_at) : "-"} />

                      <Typography variant="subtitle2" sx={{ mt: 0.8 }}>
                        Phases
                      </Typography>
                      {phaseViews.length === 0 ? (
                        <Typography variant="body2" color="text.secondary">
                          No phase templates or phases were found in the scenario payload.
                        </Typography>
                      ) : (
                        <Stack spacing={0.8}>
                          {phaseViews.map((phaseValue) => {
                            const stateKey = phaseStateKey(phaseValue.name, selectedEdge.name);
                            const phaseValidating = Boolean(phaseSubmitting[stateKey]);
                            const phaseQueryingState = Boolean(phaseQuerying[stateKey]);
                            const summary =
                              phaseQueriedSummary[stateKey] ?? latestPhaseValidationSummary(detail, phaseValue.name, selectedEdge.name);
                            const setupRoutingScripts =
                              phaseValue.name === "setup_routing"
                                ? buildSetupRoutingScriptsForPhase(detail, selectedEdge.name, phaseValue.actions, selectedEdge)
                                : [];
                            return (
                              <Accordion key={`${selectedEdge.name}-${phaseValue.index}-${phaseValue.name}`} disableGutters>
                                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                                  <Stack
                                    direction={{ xs: "column", sm: "row" }}
                                    spacing={1}
                                    alignItems={{ xs: "flex-start", sm: "center" }}
                                    sx={{ width: "100%", pr: 1 }}
                                  >
                                    <Typography variant="subtitle2" sx={{ minWidth: 180, fontWeight: 600 }}>
                                      {phaseValue.name}
                                    </Typography>
                                    <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                                      Mode: {phaseValue.mode}
                                    </Typography>
                                    <Typography variant="body2" color="text.secondary">
                                      {phaseValue.actions.length} actions
                                    </Typography>
                                    <Button
                                      size="small"
                                      variant="outlined"
                                      disabled={phaseValidating}
                                      onClick={(event) => {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        void validatePhase(phaseValue.name, selectedEdge.name);
                                      }}
                                    >
                                      {phaseValidating ? (
                                        <Stack direction="row" spacing={0.8} alignItems="center">
                                          <CircularProgress size={14} />
                                          <span>Validating...</span>
                                        </Stack>
                                      ) : (
                                        "Validate Phase"
                                      )}
                                    </Button>
                                    <Button
                                      size="small"
                                      variant="text"
                                      disabled={phaseQueryingState}
                                      onClick={(event) => {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        void queryPhaseResult(phaseValue.name, selectedEdge.name);
                                      }}
                                    >
                                      {phaseQueryingState ? (
                                        <Stack direction="row" spacing={0.8} alignItems="center">
                                          <CircularProgress size={14} />
                                          <span>Querying...</span>
                                        </Stack>
                                      ) : (
                                        "Query Result"
                                      )}
                                    </Button>
                                  </Stack>
                                </AccordionSummary>
                                <AccordionDetails>
                                  <Stack spacing={0.6}>
                                    {summary ? (
                                      <>
                                        <DetailRow
                                          label="Latest Validation Status"
                                          value={
                                            <Stack direction="row" spacing={0.8} alignItems="center">
                                              <StatusBadge value={summary.status} />
                                              <Typography variant="body2" color="text.secondary">
                                                {formatDateTime(summary.validatedAt)}
                                              </Typography>
                                            </Stack>
                                          }
                                        />
                                        <DetailRow label="Latest Validation Run" value={summary.runId ? `run ${summary.runId}` : "-"} />
                                        <DetailRow label="Latest Validation Task" value={summary.taskId || "-"} />
                                        <DetailRow
                                          label="Latest Validation Result"
                                          value={summary.resultMessage || fallbackResultMessage(summary.status)}
                                        />
                                      </>
                                    ) : (
                                      <DetailRow label="Latest Validation Run" value="-" />
                                    )}
                                    {phaseResponse[stateKey] ? <Alert severity="info">{phaseResponse[stateKey]}</Alert> : null}
                                    {phaseError[stateKey] ? <Alert severity="error">{phaseError[stateKey]}</Alert> : null}
                                    {phaseQueryError[stateKey] ? <Alert severity="error">{phaseQueryError[stateKey]}</Alert> : null}
                                    <DetailRow label="Mode" value={phaseValue.mode} />
                                    <DetailRow label="Template" value={phaseValue.template || "-"} />
                                    {phaseValue.name === "setup_routing" ? (
                                      <>
                                        <Typography variant="subtitle2" sx={{ mt: 0.8 }}>
                                          Bash Script
                                        </Typography>
                                        {setupRoutingScripts.length === 0 ? (
                                          <Typography variant="body2" color="text.secondary">
                                            No setup_routing command script available yet for this edge. Run phase validation first.
                                          </Typography>
                                        ) : (
                                          <Stack spacing={1}>
                                            {setupRoutingScripts.map((item) => (
                                              <Box key={`${selectedEdge.name}-${phaseValue.name}-${item.target}`}>
                                                <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 0.4 }}>
                                                  Target VM: {item.target}
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
                                                    fontSize: 12,
                                                    lineHeight: 1.35,
                                                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace",
                                                  }}
                                                >
                                                  {item.script}
                                                </Box>
                                              </Box>
                                            ))}
                                          </Stack>
                                        )}
                                      </>
                                    ) : null}
                                  </Stack>
                                </AccordionDetails>
                              </Accordion>
                            );
                          })}
                        </Stack>
                      )}

                      <Typography variant="subtitle2" sx={{ mt: 0.8 }}>
                        Payloads
                      </Typography>
                      {payloadViews.length === 0 ? (
                        <Typography variant="body2" color="text.secondary">
                          No payloads were found in the scenario.
                        </Typography>
                      ) : (
                        <Stack spacing={0.8}>
                          {payloadViews.map((payloadValue) => (
                            <Paper key={`${selectedEdge.name}-payload-${payloadValue.index}`} variant="outlined" sx={{ p: 1 }}>
                              <Stack
                                direction={{ xs: "column", sm: "row" }}
                                spacing={1}
                                alignItems={{ xs: "flex-start", sm: "center" }}
                                justifyContent="space-between"
                              >
                                <Typography variant="subtitle2">{payloadValue.name}</Typography>
                                <Typography variant="body2" color="text.secondary">
                                  Type: {payloadValue.payloadType}
                                </Typography>
                              </Stack>
                              {payloadValue.details.length > 0 ? (
                                <Stack spacing={0.2} sx={{ mt: 0.7 }}>
                                  {payloadValue.details.map((row, index) => (
                                    <DetailRow
                                      key={`${selectedEdge.name}-payload-${payloadValue.index}-detail-${index}`}
                                      label={row.label}
                                      value={row.value}
                                    />
                                  ))}
                                </Stack>
                              ) : null}
                            </Paper>
                          ))}
                        </Stack>
                      )}
                    </Stack>
                  </Paper>
                );
              })()
            ) : null}
          </Stack>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
          <Typography variant="h6">Raw Scenario YAML</Typography>
          <Button size="small" variant="outlined" onClick={() => void handleCopyRawYaml()} disabled={!rawYaml}>
            {rawYamlCopyStatus === "copied" ? "Copied" : "Copy"}
          </Button>
        </Stack>
        {rawYamlCopyStatus === "failed" ? (
          <Alert severity="warning" sx={{ mb: 1 }}>
            Unable to copy YAML to clipboard in this browser context.
          </Alert>
        ) : null}
        {rawYaml ? (
          <Box
            sx={{
              m: 0,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "rgba(0,0,0,0.04)",
              overflowX: "auto",
            }}
          >
            <SyntaxHighlighter
              language="yaml"
              style={oneDark}
              customStyle={{
                margin: 0,
                padding: "12px",
                fontSize: "12px",
                lineHeight: 1.35,
                background: "transparent",
              }}
              wrapLongLines
            >
              {rawYaml}
            </SyntaxHighlighter>
          </Box>
        ) : (
          <Typography variant="body2" color="text.secondary">
            Raw YAML is not available for this scenario.
          </Typography>
        )}
      </Paper>
    </Stack>
  );
}
