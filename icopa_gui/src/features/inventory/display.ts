export interface DisplayRow {
  label: string;
  value: string;
}

const numberFormatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 });

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asNumber(value: unknown): number | undefined {
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

function asGpuLines(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item).trim()).filter(Boolean);
}

function formatGpu(gpuLines: string[], compact: boolean): string {
  if (gpuLines.length === 0) {
    return "-";
  }
  if (!compact || gpuLines.length === 1) {
    return gpuLines.join(" | ");
  }
  return `${gpuLines[0]} (+${gpuLines.length - 1} more)`;
}

function formatObjectInline(value: Record<string, unknown>): string {
  const entries = Object.entries(value);
  if (entries.length === 0) {
    return "-";
  }
  return entries.map(([key, item]) => `${humanizeKey(key)}: ${formatDisplayValue(item)}`).join("; ");
}

export function humanizeKey(value: string): string {
  const normalized = value
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .trim();
  if (!normalized) {
    return value;
  }
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

export function formatDisplayValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || "-";
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? numberFormatter.format(value) : "-";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return "-";
    }
    return value
      .map((item) => {
        if (isRecord(item)) {
          return formatObjectInline(item);
        }
        return formatDisplayValue(item);
      })
      .filter((item) => item !== "-")
      .join(" | ");
  }
  if (isRecord(value)) {
    return formatObjectInline(value);
  }
  return String(value);
}

export function getSystemInfoSummary(systemInfo?: Record<string, unknown>): DisplayRow[] {
  const cpuCores = asNumber(systemInfo?.cpu_cores);
  const memTotalGb = asNumber(systemInfo?.mem_total_gb);
  const memTotalKb = asNumber(systemInfo?.mem_total_kb);
  const gpuLines = asGpuLines(systemInfo?.gpu);

  const ram = memTotalGb !== undefined
    ? `${numberFormatter.format(memTotalGb)} GB`
    : memTotalKb !== undefined
      ? `${numberFormatter.format(memTotalKb / (1024 * 1024))} GB`
      : "-";

  return [
    {
      label: "CPU Cores",
      value: cpuCores !== undefined ? String(Math.trunc(cpuCores)) : "-",
    },
    {
      label: "RAM",
      value: ram,
    },
    {
      label: "GPU",
      value: formatGpu(gpuLines, true),
    },
  ];
}

export function getSystemInfoRows(systemInfo?: Record<string, unknown>): DisplayRow[] {
  const summaryRows = getSystemInfoSummary(systemInfo);
  const reservedKeys = new Set(["cpu_cores", "mem_total_kb", "mem_total_gb", "gpu"]);
  const extraRows = Object.entries(systemInfo ?? {})
    .filter(([key]) => !reservedKeys.has(key))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => ({
      label: humanizeKey(key),
      value: formatDisplayValue(value),
    }));
  return [...summaryRows, ...extraRows];
}

export function getObjectRows(data?: Record<string, unknown>): DisplayRow[] {
  return Object.entries(data ?? {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => ({
      label: humanizeKey(key),
      value: formatDisplayValue(value),
    }));
}
