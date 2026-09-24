import type { ProfilingGeneratedRun } from "../types";

type JsonRecord = Record<string, unknown>;

export interface ComparisonSummaryRow {
  runId: number;
  experimentName: string;
  genVersion: string;
  planRunId: string;
  status: string;
  payload: string;
  publishRate: string;
  runtimeEnv: string;
  edgeFrom: string;
  edgeTo: string;
}

function asRecord(value: unknown): JsonRecord | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as JsonRecord;
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

function splitEdgePair(link: string, edgeFrom: string, edgeTo: string): { from: string; to: string } {
  if (edgeFrom && edgeTo) {
    return { from: edgeFrom, to: edgeTo };
  }
  if (!link) {
    return { from: "-", to: "-" };
  }
  const parts = link.split("->").map((item) => item.trim()).filter((item) => !!item);
  if (parts.length >= 2) {
    return { from: parts[0], to: parts[1] };
  }
  return { from: "-", to: "-" };
}

export function buildComparisonSummaryRow(run: ProfilingGeneratedRun): ComparisonSummaryRow {
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
  const pair = splitEdgePair(directLink, edgeFrom, edgeTo);

  return {
    runId: run.id,
    experimentName: run.experiment_name ?? run.generated_plan_name ?? "-",
    genVersion: run.gen_version ? `v${run.gen_version}` : "-",
    planRunId: run.plan_run_id || "-",
    status: (run.status ?? "").toUpperCase() || "UNKNOWN",
    payload: payload || "-",
    publishRate: publishRate || "-",
    runtimeEnv: runtimeEnv || "-",
    edgeFrom: pair.from,
    edgeTo: pair.to,
  };
}
