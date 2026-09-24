import {
  Button,
  Chip,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { useEffect } from "react";
import { Link as RouterLink } from "react-router-dom";

import { EmptyState } from "../../../shared/components/EmptyState";
import { ErrorState } from "../../../shared/components/ErrorState";
import { LoadingState } from "../../../shared/components/LoadingState";
import { useExperimentStore } from "../../../stores/experiment.store";

export function StoredComparisionListPage() {
  const comparisions = useExperimentStore((state) => state.comparisions);
  const loading = useExperimentStore((state) => state.loading);
  const error = useExperimentStore((state) => state.error);
  const fetchComparisions = useExperimentStore((state) => state.fetchComparisions);

  useEffect(() => {
    void fetchComparisions();
  }, [fetchComparisions]);

  if (loading && comparisions.length === 0) {
    return <LoadingState label="Loading comparisions..." />;
  }

  if (error && comparisions.length === 0) {
    return <ErrorState message={error} />;
  }

  if (comparisions.length === 0) {
    return <EmptyState label="No saved comparisions." />;
  }

  return (
    <Stack spacing={2}>
      <Typography variant="h5">ComparisionStore</Typography>
      {error ? <ErrorState message={error} /> : null}
      <Stack spacing={1.2}>
        {comparisions.map((item) => (
          <Paper key={item.id ?? item.name} sx={{ p: 1.4 }}>
            <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
              <Stack spacing={0.4}>
                <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                  {item.name}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {item.description || "No description"}
                </Typography>
              </Stack>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="body2" color="text.secondary">
                  Runs: {item.run_count ?? item.runs?.length ?? 0}
                </Typography>
                {item.id ? (
                  <Button
                    component={RouterLink}
                    to={`/experiment/comparision_store/${item.id}`}
                    variant="outlined"
                    size="small"
                  >
                    Open
                  </Button>
                ) : null}
              </Stack>
            </Stack>
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mt: 1 }}>
              {(item.runs ?? []).map((runItem) => (
                <Chip
                  key={`${item.id}-${runItem.id}-${runItem.run_id}`}
                  label={
                    runItem.deleted
                      ? `Run #${runItem.run_id} (deleted)`
                      : `Run #${runItem.run_id} ${runItem.status ? `(${runItem.status})` : ""}`
                  }
                  color={runItem.deleted ? "default" : "primary"}
                  variant={runItem.deleted ? "outlined" : "filled"}
                  size="small"
                />
              ))}
            </Stack>
          </Paper>
        ))}
      </Stack>
    </Stack>
  );
}
