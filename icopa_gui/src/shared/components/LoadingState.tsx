import { CircularProgress, Paper, Stack, Typography } from "@mui/material";

export function LoadingState({ label = "Loading..." }: { label?: string }) {
  return (
    <Paper sx={{ p: 3 }}>
      <Stack direction="row" spacing={2} alignItems="center">
        <CircularProgress size={22} />
        <Typography>{label}</Typography>
      </Stack>
    </Paper>
  );
}
