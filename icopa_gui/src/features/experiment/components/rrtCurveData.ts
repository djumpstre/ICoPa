import { env } from "../../../core/config/env";
import { requestArtifactText } from "../../../core/api/artifacts";
import { useAuthStore } from "../../../stores/auth.store";
import type { ProfilingGeneratedRun } from "../types";

type JsonRecord = Record<string, unknown>;

export interface LatencyPoint {
  id: number;
  rttMs: number;
}

interface RunMetricFileUrls {
  configUrl: string;
  allCsvUrl: string;
}

interface ComparableRunRow {
  runId: string;
  metricsBackendDir: string;
  variantValues: JsonRecord;
  fileUrls?: RunMetricFileUrls;
}

interface CurveLoadResult {
  points: LatencyPoint[];
  payload: string;
  frequency: string;
}

export interface ExperimentRunCurveData {
  runId: string;
  payload: string;
  frequency: string;
  frequencyHz?: number;
  points: LatencyPoint[];
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

async function fetchTextIfOk(url: string, signal: AbortSignal): Promise<string> {
  return requestArtifactText(url, { signal, token: useAuthStore.getState().token });
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

function extractYamlScalar(yamlText: string, key: string): string {
  const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const regex = new RegExp(`^\\s*${escapedKey}:\\s*(.+)\\s*$`, "m");
  const match = yamlText.match(regex);
  return match ? match[1].trim().replace(/^['"]|['"]$/g, "") : "";
}

function pickVariantValue(variantValues: JsonRecord, predicate: (key: string) => boolean): string {
  const sortedEntries = Object.entries(variantValues).sort(([left], [right]) => left.localeCompare(right));
  const match = sortedEntries.find(([key]) => predicate(key.toLowerCase()));
  if (!match) {
    return "";
  }
  return getString(match[1]);
}

function extractPayloadAndFrequency(configText: string, variantValues: JsonRecord): { payload: string; frequency: string } {
  const payloadFromConfig =
    extractYamlScalar(configText, "payload_size") ||
    extractYamlScalar(configText, "payload") ||
    extractYamlScalar(configText, "message_size");

  const frequencyFromConfig =
    extractYamlScalar(configText, "frequency_hz") ||
    extractYamlScalar(configText, "publish_frequency_hz") ||
    extractYamlScalar(configText, "publish_frequency") ||
    extractYamlScalar(configText, "frequency");

  const payloadFromVariant =
    pickVariantValue(variantValues, (key) => key.includes("payload") && !key.includes("response")) ||
    pickVariantValue(variantValues, (key) => key.includes("message_size"));
  const frequencyFromVariant =
    pickVariantValue(variantValues, (key) => key.includes("frequency")) ||
    pickVariantValue(variantValues, (key) => key.includes("freq")) ||
    pickVariantValue(variantValues, (key) => key.endsWith("_hz"));

  return {
    payload: payloadFromConfig || payloadFromVariant || "-",
    frequency: frequencyFromConfig || frequencyFromVariant || "-",
  };
}

function parseHz(value: string): number | undefined {
  const parsed = Number(value);
  if (Number.isFinite(parsed) && parsed > 0) {
    return parsed;
  }
  const matched = value.match(/-?\d+(\.\d+)?/);
  if (!matched) {
    return undefined;
  }
  const extracted = Number(matched[0]);
  return Number.isFinite(extracted) && extracted > 0 ? extracted : undefined;
}

function buildRunMetricFileMap(detail: ProfilingGeneratedRun): Record<string, RunMetricFileUrls> {
  const map: Record<string, RunMetricFileUrls> = {};
  asRecordArray(detail.run_metric_files).forEach((item) => {
    const runId = getString(item.run_id);
    if (!runId) {
      return;
    }
    map[runId] = {
      configUrl: getString(item.rrt_config_url),
      allCsvUrl: getString(item.rrt_all_csv_url),
    };
  });
  return map;
}

function extractComparableRows(detail: ProfilingGeneratedRun): ComparableRunRow[] {
  const rows = new Map<string, ComparableRunRow>();
  const fileMap = buildRunMetricFileMap(detail);
  const snapshotRuns = asRecord(asRecord(detail.rrt_config_execution_snapshot)?.runs) ?? {};

  asRecordArray(detail.results).forEach((runItem, index) => {
    const runId = getString(runItem.run_id) || `run-${String(index + 1).padStart(3, "0")}`;
    if (!runId) {
      return;
    }
    const snapshotRun = asRecord(snapshotRuns[runId]);
    const variantValues = asRecord(runItem.variant_values) ?? asRecord(snapshotRun?.variant_values) ?? {};

    rows.set(runId, {
      runId,
      metricsBackendDir: getString(runItem.metrics_backend_dir),
      variantValues,
      fileUrls: fileMap[runId],
    });
  });

  Object.entries(snapshotRuns).forEach(([runId, runItem]) => {
    const snapshotRun = asRecord(runItem);
    const existing = rows.get(runId);
    const variantValues = asRecord(snapshotRun?.variant_values) ?? {};
    if (existing) {
      rows.set(runId, {
        ...existing,
        variantValues: Object.keys(existing.variantValues).length > 0 ? existing.variantValues : variantValues,
      });
      return;
    }
    rows.set(runId, {
      runId,
      metricsBackendDir: "",
      variantValues,
      fileUrls: fileMap[runId],
    });
  });

  Object.keys(fileMap).forEach((runId) => {
    if (rows.has(runId)) {
      return;
    }
    rows.set(runId, {
      runId,
      metricsBackendDir: "",
      variantValues: {},
      fileUrls: fileMap[runId],
    });
  });

  return [...rows.values()].sort((left, right) => left.runId.localeCompare(right.runId));
}

async function loadCurveData(
  detail: ProfilingGeneratedRun,
  row: ComparableRunRow,
  signal: AbortSignal,
): Promise<CurveLoadResult | null> {
  const directCsvUrl = toBackendUrl(row.fileUrls?.allCsvUrl ?? "");
  const directConfigUrl = toBackendUrl(row.fileUrls?.configUrl ?? "");
  if (directCsvUrl) {
    const [csvText, configText] = await Promise.all([
      fetchTextIfOk(directCsvUrl, signal),
      directConfigUrl ? fetchTextIfOk(directConfigUrl, signal) : Promise.resolve(""),
    ]);
    const points = parseLatencyCsv(csvText);
    if (points.length > 0) {
      const { payload, frequency } = extractPayloadAndFrequency(configText, row.variantValues);
      return { points, payload, frequency };
    }
  }

  const baseCandidates = [
    detail.collected_metrics_url ? `${toBackendUrl(detail.collected_metrics_url)}/${row.runId}` : "",
    toBackendUrl(row.metricsBackendDir),
  ].filter((item) => !!item);

  for (const base of [...new Set(baseCandidates)]) {
    const vmHomeBase = `${normalizeBaseUrl(base)}/vm_home`;
    const csvText = await fetchTextIfOk(`${vmHomeBase}/rrt_all.csv`, signal);
    if (!csvText) {
      continue;
    }
    const configText = await fetchTextIfOk(`${vmHomeBase}/rrt_config.yaml`, signal);
    const points = parseLatencyCsv(csvText);
    if (points.length === 0) {
      continue;
    }
    const { payload, frequency } = extractPayloadAndFrequency(configText, row.variantValues);
    return { points, payload, frequency };
  }

  return null;
}

export async function loadCurvesFromExperimentRun(
  detail: ProfilingGeneratedRun,
  signal: AbortSignal,
): Promise<ExperimentRunCurveData[]> {
  const rows = extractComparableRows(detail);
  const curves: ExperimentRunCurveData[] = [];

  for (const row of rows) {
    const result = await loadCurveData(detail, row, signal);
    if (!result) {
      continue;
    }
    curves.push({
      runId: row.runId,
      payload: result.payload,
      frequency: result.frequency,
      frequencyHz: parseHz(result.frequency),
      points: result.points,
    });
  }

  return curves;
}
