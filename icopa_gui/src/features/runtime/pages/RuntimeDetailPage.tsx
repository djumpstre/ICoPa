import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import RefreshIcon from "@mui/icons-material/Refresh";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  IconButton,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { type ReactNode, useCallback, useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import { SyntaxHighlighter, oneDark } from "../../../shared/components/SyntaxHighlighter";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { formatDateTime } from "../../../shared/utils/date";
import { useRuntimeStore } from "../../../stores/runtime.store";

type JsonRecord = Record<string, unknown>;

interface CommandPresetView {
  index: number;
  name: string;
  group: string;
  executor: string;
  command: string;
  details: Array<{ label: string; value: string }>;
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

function getText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
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

function buildCommandPresetView(value: JsonRecord, index: number): CommandPresetView {
  return {
    index: index + 1,
    name: getString(value.name) || `preset-${index + 1}`,
    group: getString(value.group),
    executor: getString(value.executor),
    command: getString(value.command),
    details: flattenRows(omitKeys(value, ["name", "group", "executor", "command"])),
  };
}

function toBashScript(command: string): string {
  const trimmed = command.trim();
  if (!trimmed) {
    return "";
  }
  return `#!/usr/bin/env bash
set -euo pipefail

${trimmed}`;
}

function yamlScalar(value: unknown): string {
  if (value == null) {
    return "null";
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) {
      return '""';
    }
    if (/[:#\-\n]/.test(trimmed) || /\s/.test(trimmed)) {
      return JSON.stringify(trimmed);
    }
    return trimmed;
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "number") {
    return String(value);
  }
  return JSON.stringify(value);
}

function renderYaml(value: unknown, indent = 0): string[] {
  const pad = "  ".repeat(indent);
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return [`${pad}[]`];
    }
    const lines: string[] = [];
    value.forEach((item) => {
      if (item && typeof item === "object") {
        lines.push(`${pad}-`);
        lines.push(...renderYaml(item, indent + 1));
      } else {
        lines.push(`${pad}- ${yamlScalar(item)}`);
      }
    });
    return lines;
  }

  const record = asRecord(value);
  if (record) {
    const keys = Object.keys(record);
    if (keys.length === 0) {
      return [`${pad}{}`];
    }
    const lines: string[] = [];
    keys.forEach((key) => {
      const item = record[key];
      if (item && typeof item === "object") {
        lines.push(`${pad}${key}:`);
        lines.push(...renderYaml(item, indent + 1));
      } else {
        lines.push(`${pad}${key}: ${yamlScalar(item)}`);
      }
    });
    return lines;
  }

  return [`${pad}${yamlScalar(value)}`];
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

export function RuntimeDetailPage() {
  const params = useParams<{ envName: string }>();
  const envName = params.envName ?? "";

  const detail = useRuntimeStore((state) => state.detailByName[envName]);
  const loadingDetail = useRuntimeStore((state) => state.loadingDetail);
  const error = useRuntimeStore((state) => state.error);
  const fetchDetail = useRuntimeStore((state) => state.fetchDetail);

  const refreshDetail = useCallback(() => {
    if (envName) {
      void fetchDetail(envName);
    }
  }, [envName, fetchDetail]);

  useEffect(() => {
    refreshDetail();
  }, [refreshDetail]);

  useEffect(() => {
    if (!envName) {
      return undefined;
    }
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        refreshDetail();
      }
    };
    window.addEventListener("focus", refreshDetail);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", refreshDetail);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [envName, refreshDetail]);

  const detailRecord = asRecord(detail) ?? {};
  const rrtConfigUrl = getString(detailRecord.serializer_rrt_config_url);

  if (!envName) {
    return <EmptyState label="Missing runtime environment name." />;
  }

  if (loadingDetail && !detail) {
    return <LoadingState label="Loading runtime environment detail..." />;
  }
  if (error && !detail) {
    return <ErrorState message={error} />;
  }
  if (!detail) {
    return <EmptyState label={`Runtime environment ${envName} was not found.`} />;
  }

  const metadataRows = flattenRows(asRecord(detail.metadata) ?? {});
  const tagRows = flattenRows(asRecord(detail.tags) ?? {});
  const parameterRecord = asRecord(detail.parameters) ?? {};
  const parameterKeys = Object.keys(parameterRecord).sort();
  const imageRows = flattenRows(asRecord(detail.images) ?? {});
  const commandGroupRows = flattenRows(asRecord(detail.command_groups) ?? {});
  const commandPresetViews = asRecordArray(detail.command_preset).map((item, index) => buildCommandPresetView(item, index));
  const rrtConfigJson = detail.serializer_rrt_config_json;
  const rrtConfigRawYaml = getText(detail.serializer_rrt_config_raw_yaml);
  const hasRrtConfigFromJson = !!(
    (Array.isArray(rrtConfigJson) && rrtConfigJson.length > 0) ||
    (asRecord(rrtConfigJson) && Object.keys(asRecord(rrtConfigJson) ?? {}).length > 0)
  );
  const hasRrtConfig = rrtConfigRawYaml.trim().length > 0 || hasRrtConfigFromJson;
  const rrtYamlText = rrtConfigRawYaml.trim().length > 0 ? rrtConfigRawYaml : renderYaml(rrtConfigJson).join("\n");

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h5">Runtime Environment Detail</Typography>
        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            size="small"
            startIcon={<RefreshIcon fontSize="small" />}
            onClick={refreshDetail}
          >
            Refresh
          </Button>
          <Button component={Link} to="/runtime" variant="outlined" size="small">
            Back to list
          </Button>
        </Stack>
      </Stack>
      {error && <ErrorState message={error} />}
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6">{detail.name}</Typography>
        <DetailRow label="Kind" value={detail.kind ?? "-"} />
        <DetailRow label="Command Presets" value={String(commandPresetViews.length)} />
        <DetailRow label="Command Groups" value={String(Object.keys(asRecord(detail.command_groups) ?? {}).length)} />
        <DetailRow label="Created" value={formatDateTime(detail.created_at)} />
        <DetailRow label="Updated" value={formatDateTime(detail.updated_at)} />
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Metadata
        </Typography>
        {metadataRows.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No metadata fields.
          </Typography>
        ) : (
          metadataRows.map((row, index) => <DetailRow key={`metadata-${index}`} label={row.label} value={row.value} />)
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Tags
        </Typography>
        {tagRows.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No tags defined.
          </Typography>
        ) : (
          tagRows.map((row, index) => <DetailRow key={`tags-${index}`} label={row.label} value={row.value} />)
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Parameters
        </Typography>
        {parameterKeys.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No parameter values.
          </Typography>
        ) : (
          <Stack spacing={0.8}>
            {parameterKeys.map((parameterKey) => {
              const value = parameterRecord[parameterKey];
              const nested = asRecord(value);
              return (
                <Paper key={parameterKey} variant="outlined" sx={{ p: 1 }}>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    {humanizeKey(parameterKey)}
                  </Typography>
                  {nested ? (
                    <Stack spacing={0.15}>
                      {flattenRows(nested).map((row, index) => (
                        <DetailRow key={`${parameterKey}-${index}`} label={row.label} value={row.value} />
                      ))}
                    </Stack>
                  ) : (
                    <DetailRow label="Value" value={toDisplayValue(value)} />
                  )}
                </Paper>
              );
            })}
          </Stack>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
          <Typography variant="h6">RRT Config YAML</Typography>
          <Tooltip title="Copy YAML">
            <span>
              <IconButton
                size="small"
                onClick={() => {
                  void copyPlainText(rrtYamlText);
                }}
                disabled={!hasRrtConfig}
                aria-label="copy rrt yaml"
              >
                <ContentCopyIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>
        <DetailRow label="Config Path" value={detail.serializer_rrt_config_path || "-"} />
        <DetailRow label="Config URL" value={rrtConfigUrl || detail.serializer_rrt_config_file || "-"} />
        {hasRrtConfig ? (
          <Box
            sx={{
              m: 0,
              mt: 0.8,
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
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
                lineHeight: 1.45,
                background: "transparent",
              }}
              wrapLongLines
            >
              {rrtYamlText}
            </SyntaxHighlighter>
          </Box>
        ) : (
          <Typography variant="body2" color="text.secondary">
            No serializer_rrt_config.yaml content available.
          </Typography>
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Images
        </Typography>
        {imageRows.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No image mappings.
          </Typography>
        ) : (
          imageRows.map((row, index) => <DetailRow key={`image-${index}`} label={row.label} value={row.value} />)
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Command Groups
        </Typography>
        {commandGroupRows.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No command groups.
          </Typography>
        ) : (
          commandGroupRows.map((row, index) => <DetailRow key={`group-${index}`} label={row.label} value={row.value} />)
        )}
      </Paper>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Command Presets
        </Typography>
        {commandPresetViews.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No command presets.
          </Typography>
        ) : (
          <Stack spacing={0.8}>
            {commandPresetViews.map((presetValue) => (
              <Accordion defaultExpanded key={`${presetValue.index}-${presetValue.name}`} disableGutters>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Stack
                    direction={{ xs: "column", sm: "row" }}
                    spacing={1}
                    alignItems={{ xs: "flex-start", sm: "center" }}
                    sx={{ width: "100%", pr: 1 }}
                  >
                    <Typography variant="subtitle2" sx={{ minWidth: 220, fontWeight: 600 }}>
                      {presetValue.name}
                    </Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                      Group: {presetValue.group || "-"}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Executor: {presetValue.executor || "-"}
                    </Typography>
                  </Stack>
                </AccordionSummary>
                <AccordionDetails>
                  <Stack spacing={0.5}>
                    <Stack direction="row" justifyContent="space-between" alignItems="center">
                      <Typography variant="body2" color="text.secondary">
                        Bash Script
                      </Typography>
                      <Tooltip title="Copy Script">
                        <IconButton
                          size="small"
                          onClick={() => {
                            void copyPlainText(toBashScript(presetValue.command) || "# (no command)");
                          }}
                          aria-label={`copy bash script ${presetValue.name}`}
                        >
                          <ContentCopyIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Stack>
                    <Box
                      sx={{
                        m: 0,
                        borderRadius: 1,
                        border: "1px solid",
                        borderColor: "divider",
                        overflowX: "auto",
                      }}
                    >
                      <SyntaxHighlighter
                        language="bash"
                        style={oneDark}
                        customStyle={{
                          margin: 0,
                          padding: "10px",
                          fontSize: "12px",
                          lineHeight: 1.4,
                          background: "transparent",
                        }}
                        wrapLongLines
                      >
                        {toBashScript(presetValue.command) || "# (no command)"}
                      </SyntaxHighlighter>
                    </Box>
                    {presetValue.details.map((row, index) => (
                      <DetailRow key={`${presetValue.name}-detail-${index}`} label={row.label} value={row.value} />
                    ))}
                  </Stack>
                </AccordionDetails>
              </Accordion>
            ))}
          </Stack>
        )}
      </Paper>
    </Stack>
  );
}
