import { Chip } from "@mui/material";

const statusToColor: Record<string, "default" | "success" | "warning" | "error" | "info"> = {
  RUNNING: "warning",
  STARTING: "info",
  STOPPING: "warning",
  STOPPED: "default",
  DEALLOCATED: "default",
  PENDING: "info",
  READY: "info",
  STARTED: "warning",
  SUCCEEDED: "success",
  SUCCESS: "success",
  PASS: "success",
  FAILED: "error",
  FAIL: "error",
  ERROR: "error",
  IDLE: "default",
};

export function StatusBadge({ value }: { value?: string | null }) {
  const text = (value ?? "UNKNOWN").toUpperCase();
  return <Chip label={text} size="small" color={statusToColor[text] ?? "default"} variant="outlined" />;
}
