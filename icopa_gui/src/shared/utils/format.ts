export function formatJsonPreview(value: unknown, maxLength = 120): string {
  const raw = JSON.stringify(value);
  if (raw.length <= maxLength) {
    return raw;
  }
  return `${raw.slice(0, maxLength)}...`;
}

export function toArray<T>(value: T[] | undefined | null): T[] {
  return Array.isArray(value) ? value : [];
}
