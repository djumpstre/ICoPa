import { Button, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TableSortLabel } from "@mui/material";
import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import {
  formatBytesAbbr,
  formatOneDigit,
  formatStat,
  type RunMeasurementStatsRow,
} from "./runMeasurementStats";

export interface RunMeasurementStatsTableRow extends RunMeasurementStatsRow {
  deleted?: boolean;
}

interface RunMeasurementStatsTableProps {
  rows: RunMeasurementStatsTableRow[];
  initialSortKey?: MeasurementSortKey;
  initialSortDirection?: "asc" | "desc";
  actionHeader?: string;
  renderAction?: (row: RunMeasurementStatsTableRow) => ReactNode;
}

type MeasurementSortKey =
  | "runDbId"
  | "vmLink"
  | "payloadBytes"
  | "pubRateHz"
  | "estUploadMbps"
  | "estDownloadMbps"
  | "meanRttMs"
  | "p95Ms"
  | "p99Ms"
  | "packetLossPercent";

function compareBySortKey(
  left: RunMeasurementStatsTableRow,
  right: RunMeasurementStatsTableRow,
  sortKey: MeasurementSortKey,
  sortDirection: "asc" | "desc",
): number {
  const factor = sortDirection === "asc" ? 1 : -1;
  const leftValue = left[sortKey];
  const rightValue = right[sortKey];
  if (typeof leftValue === "string" || typeof rightValue === "string") {
    return String(leftValue ?? "").toLowerCase().localeCompare(String(rightValue ?? "").toLowerCase()) * factor;
  }
  if (typeof leftValue === "number" && Number.isFinite(leftValue) && typeof rightValue === "number" && Number.isFinite(rightValue)) {
    return (leftValue - rightValue) * factor;
  }
  if (typeof leftValue === "number" && Number.isFinite(leftValue)) {
    return -1 * factor;
  }
  if (typeof rightValue === "number" && Number.isFinite(rightValue)) {
    return 1 * factor;
  }
  return (left.runDbId - right.runDbId) * factor;
}

function SortableHeader({
  label,
  sortKey,
  activeSortKey,
  sortDirection,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: MeasurementSortKey;
  activeSortKey: MeasurementSortKey;
  sortDirection: "asc" | "desc";
  onSort: (sortKey: MeasurementSortKey) => void;
  align?: "left" | "right";
}) {
  return (
    <TableCell align={align} sortDirection={activeSortKey === sortKey ? sortDirection : false}>
      <TableSortLabel
        active={activeSortKey === sortKey}
        direction={activeSortKey === sortKey ? sortDirection : "asc"}
        onClick={() => onSort(sortKey)}
      >
        {label}
      </TableSortLabel>
    </TableCell>
  );
}

function renderVmLink(vmLink: string, deleted = false): ReactNode {
  const parts = vmLink
    .split("->")
    .map((item) => item.trim())
    .filter((item) => Boolean(item));
  if (parts.length === 2 && !deleted) {
    const [fromVm, toVm] = parts;
    return (
      <>
        <Button
          component={RouterLink}
          to={`/inventory/${encodeURIComponent(fromVm)}`}
          variant="text"
          size="small"
          sx={{ minWidth: 0, p: 0 }}
        >
          {fromVm}
        </Button>
        {" -> "}
        <Button
          component={RouterLink}
          to={`/inventory/${encodeURIComponent(toVm)}`}
          variant="text"
          size="small"
          sx={{ minWidth: 0, p: 0 }}
        >
          {toVm}
        </Button>
      </>
    );
  }
  if (parts.length === 1 && parts[0] !== "-" && !deleted) {
    const vmName = parts[0];
    return (
      <Button
        component={RouterLink}
        to={`/inventory/${encodeURIComponent(vmName)}`}
        variant="text"
        size="small"
        sx={{ minWidth: 0, p: 0 }}
      >
        {vmName}
      </Button>
    );
  }
  return vmLink || "-";
}

export function RunMeasurementStatsTable({
  rows,
  initialSortKey = "runDbId",
  initialSortDirection = "asc",
  actionHeader,
  renderAction,
}: RunMeasurementStatsTableProps) {
  const [sortKey, setSortKey] = useState<MeasurementSortKey>(initialSortKey);
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">(initialSortDirection);

  const sortedRows = useMemo(
    () => [...rows].sort((left, right) => compareBySortKey(left, right, sortKey, sortDirection)),
    [rows, sortDirection, sortKey],
  );

  const onSort = (nextKey: MeasurementSortKey): void => {
    if (sortKey === nextKey) {
      setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(nextKey);
    setSortDirection("asc");
  };

  return (
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow>
            <SortableHeader
              label="Run"
              sortKey="runDbId"
              activeSortKey={sortKey}
              sortDirection={sortDirection}
              onSort={onSort}
            />
            <SortableHeader
              label="Link"
              sortKey="vmLink"
              activeSortKey={sortKey}
              sortDirection={sortDirection}
              onSort={onSort}
            />
            <SortableHeader
              label="Payload"
              sortKey="payloadBytes"
              activeSortKey={sortKey}
              sortDirection={sortDirection}
              onSort={onSort}
              align="right"
            />
            <SortableHeader
              label="Pub_Rate"
              sortKey="pubRateHz"
              activeSortKey={sortKey}
              sortDirection={sortDirection}
              onSort={onSort}
              align="right"
            />
            <SortableHeader
              label="Up BW"
              sortKey="estUploadMbps"
              activeSortKey={sortKey}
              sortDirection={sortDirection}
              onSort={onSort}
              align="right"
            />
            <SortableHeader
              label="Down BW"
              sortKey="estDownloadMbps"
              activeSortKey={sortKey}
              sortDirection={sortDirection}
              onSort={onSort}
              align="right"
            />
            <SortableHeader
              label="Mean RTT"
              sortKey="meanRttMs"
              activeSortKey={sortKey}
              sortDirection={sortDirection}
              onSort={onSort}
              align="right"
            />
            <SortableHeader
              label="P95"
              sortKey="p95Ms"
              activeSortKey={sortKey}
              sortDirection={sortDirection}
              onSort={onSort}
              align="right"
            />
            <SortableHeader
              label="P99"
              sortKey="p99Ms"
              activeSortKey={sortKey}
              sortDirection={sortDirection}
              onSort={onSort}
              align="right"
            />
            <SortableHeader
              label="Packet Loss"
              sortKey="packetLossPercent"
              activeSortKey={sortKey}
              sortDirection={sortDirection}
              onSort={onSort}
              align="right"
            />
            {renderAction ? <TableCell align="center">{actionHeader ?? "Action"}</TableCell> : null}
          </TableRow>
        </TableHead>
        <TableBody>
          {sortedRows.map((row) => (
            <TableRow key={`measurement-row-${row.runDbId}`} sx={row.deleted ? { opacity: 0.75 } : undefined}>
              <TableCell>
                {row.deleted ? (
                  `Run #${row.runDbId}`
                ) : (
                  <Button
                    component={RouterLink}
                    to={`/experiment/runs/${row.runDbId}`}
                    variant="text"
                    size="small"
                    sx={{ minWidth: 0, p: 0, textTransform: "none" }}
                  >
                    {`Run #${row.runDbId}`}
                  </Button>
                )}
              </TableCell>
              <TableCell>{renderVmLink(row.vmLink, row.deleted)}</TableCell>
              <TableCell align="right">{formatBytesAbbr(row.payloadBytes)}</TableCell>
              <TableCell align="right">
                {row.pubRateHz == null || !Number.isFinite(row.pubRateHz) ? "-" : `${formatOneDigit(row.pubRateHz)}Hz`}
              </TableCell>
              <TableCell align="right">
                {row.estUploadMbps == null || !Number.isFinite(row.estUploadMbps)
                  ? "-"
                  : `${formatOneDigit(row.estUploadMbps)}Mbps`}
              </TableCell>
              <TableCell align="right">
                {row.estDownloadMbps == null || !Number.isFinite(row.estDownloadMbps)
                  ? "-"
                  : `${formatOneDigit(row.estDownloadMbps)}Mbps`}
              </TableCell>
              <TableCell align="right">
                {row.meanRttMs == null || !Number.isFinite(row.meanRttMs) ? "-" : `${formatOneDigit(row.meanRttMs)} ms`}
              </TableCell>
              <TableCell align="right">
                {row.p95Ms == null || !Number.isFinite(row.p95Ms) ? "-" : `${formatOneDigit(row.p95Ms)} ms`}
              </TableCell>
              <TableCell align="right">
                {row.p99Ms == null || !Number.isFinite(row.p99Ms) ? "-" : `${formatOneDigit(row.p99Ms)} ms`}
              </TableCell>
              <TableCell align="right">{formatStat(row.packetLossPercent, 3, "%")}</TableCell>
              {renderAction ? <TableCell align="center">{renderAction(row)}</TableCell> : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
