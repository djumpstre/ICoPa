import { Box, Paper, Typography } from "@mui/material";

import type { InventoryVm } from "../types";
import { formatDateTime, formatDateTimeWithRelative } from "../../../shared/utils/date";

interface VmSummaryCardProps {
  vm: InventoryVm;
}

function Kv({ label, value }: { label: string; value: string }) {
  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Paper>
  );
}

export function VmSummaryCard({ vm }: VmSummaryCardProps) {
  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        {vm.name}
      </Typography>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "1fr",
            sm: "repeat(2, minmax(0, 1fr))",
            md: "repeat(3, minmax(0, 1fr))",
          },
          gap: 1.5,
        }}
      >
        <Box>
          <Kv label="Address" value={vm.address ?? "-"} />
        </Box>
        <Box>
          <Kv label="Status" value={vm.status ?? "-"} />
        </Box>
        <Box>
          <Kv label="SSH User" value={vm.user_name ?? "-"} />
        </Box>
        <Box>
          <Kv label="Port" value={String(vm.port ?? "-")} />
        </Box>
        <Box>
          <Kv label="Last Connection" value={formatDateTimeWithRelative(vm.last_connection_time)} />
        </Box>
        <Box>
          <Kv label="Updated" value={formatDateTime(vm.updated_at)} />
        </Box>
      </Box>
    </Paper>
  );
}
