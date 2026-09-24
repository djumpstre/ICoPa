export function formatDateTime(value?: string | null): string {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

export function formatRelativeSinceNow(value?: string | null): string {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }

  const diffMs = Date.now() - parsed.getTime();
  if (diffMs <= 0) {
    return "before <1 min";
  }

  const totalMinutes = Math.floor(diffMs / (60 * 1000));
  if (totalMinutes < 1) {
    return "before <1 min";
  }
  if (totalMinutes < 60) {
    return `before ${String(totalMinutes)} min`;
  }

  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `before ${String(hours)} h ${String(minutes)} min`;
}

export function formatDateTimeWithRelative(value?: string | null): string {
  const absolute = formatDateTime(value);
  if (absolute === "-") {
    return absolute;
  }
  const relative = formatRelativeSinceNow(value);
  if (!relative) {
    return absolute;
  }
  return `${absolute} (${relative})`;
}
