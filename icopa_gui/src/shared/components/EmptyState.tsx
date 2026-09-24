import { Paper, Typography } from "@mui/material";

export function EmptyState({ label = "No data available." }: { label?: string }) {
  return (
    <Paper sx={{ p: 3 }}>
      <Typography color="text.secondary">{label}</Typography>
    </Paper>
  );
}
