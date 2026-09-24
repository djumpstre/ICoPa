import type { ProfilingGeneratedRun } from "../types";

type JsonRecord = Record<string, unknown>;

export interface RunMeasurementStatsRow {
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
  return value.filter((item): item is JsonRecord => Boolean(asRecord(item)));
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

function extractVmLinkFromMetadata(runDetail: ProfilingGeneratedRun): string {
  const characterization = asRecord(runDetail.characterization_parameters);
  const runMetadata = asRecord(runDetail.run_metadata);
  const edge = asRecord(characterization?.edge) ?? asRecord(runMetadata?.edge);
  if (edge) {
    const from = getString(edge.from);
    const to = getString(edge.to);
    if (from && to) {
      return `${from} -> ${to}`;
    }
  }
  const link = getString(characterization?.link) || getString(runMetadata?.link);
  return link || "";
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

  return extractVmLinkFromMetadata(runDetail) || "-";
}

export function formatOneDigit(value?: number): string {
  if (value == null || !Number.isFinite(value)) {
    return "-";
  }
  const text = value.toFixed(1);
  return text.endsWith(".0") ? text.slice(0, -2) : text;
}

export function formatBytesAbbr(value?: number): string {
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

export function formatStat(value?: number, digits = 3, unit = "ms"): string {
  if (value == null || !Number.isFinite(value)) {
    return "-";
  }
  return `${value.toFixed(digits)}${unit ? ` ${unit}` : ""}`;
}

export function buildRunMeasurementStatsRow(runDetail: ProfilingGeneratedRun): RunMeasurementStatsRow {
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
  const means = summaries.map((item) => item.mean).filter((value): value is number => value != null);
  const p95s = summaries.map((item) => item.p95).filter((value): value is number => value != null);
  const p99s = summaries.map((item) => item.p99).filter((value): value is number => value != null);
  const losses = summaries.map((item) => item.loss).filter((value): value is number => value != null);
  const payloads = configRows.map((item) => item.payloadBytes).filter((value): value is number => value != null);
  const responses = configRows.map((item) => item.responsePayloadBytes).filter((value): value is number => value != null);
  const pubRates = configRows.map((item) => item.pubRateHz).filter((value): value is number => value != null);
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
  };
}

