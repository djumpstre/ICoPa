import CompareArrowsIcon from "@mui/icons-material/CompareArrows";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Paper,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Link as RouterLink } from "react-router-dom";

import { useRunComparisonStore } from "../../../stores/runComparison.store";
import type { LatencyPoint } from "./rrtCurveData";

export interface RttCurveLine {
  key: string;
  label: string;
  runNumber: number;
  frequencyHz?: number;
  points: LatencyPoint[];
}

interface RttCurveOverlayProps {
  curves: RttCurveLine[];
  title?: string;
  buildRunLink?: (runId: number) => string;
}

function lineColor(index: number): string {
  const hue = (index * 43) % 360;
  return `hsl(${hue} 72% 42%)`;
}

function formatTooltipValue(value: number | string | Array<number | string>): string {
  const scalar = Array.isArray(value) ? value[0] : value;
  const numeric = Number(scalar);
  if (Number.isFinite(numeric)) {
    return `${numeric.toFixed(3)} ms`;
  }
  return String(scalar);
}

function looksLikeNsTimeline(points: LatencyPoint[]): boolean {
  if (points.length === 0) {
    return false;
  }
  const maxX = Math.max(...points.map((point) => point.id));
  return Number.isFinite(maxX) && maxX > 1_000_000;
}

export function RttCurveOverlay({ curves, title = "RTT Curves Overlay", buildRunLink }: RttCurveOverlayProps) {
  const [xAxisMode, setXAxisMode] = useState<"raw" | "normalized">("normalized");
  const [visibleCurveKeys, setVisibleCurveKeys] = useState<string[] | null>(null);
  const selectedRunIds = useRunComparisonStore((state) => state.selectedRunIds);
  const toggleRunInComparison = useRunComparisonStore((state) => state.toggleRun);

  const curvesWithColor = useMemo(
    () =>
      curves.map((curve, index) => ({
        ...curve,
        color: lineColor(index),
        xIsNsTimeline: looksLikeNsTimeline(curve.points),
      })),
    [curves],
  );

  const effectiveVisibleKeys = useMemo(() => {
    const availableKeys = new Set(curvesWithColor.map((curve) => curve.key));
    const base = visibleCurveKeys ?? curvesWithColor.map((curve) => curve.key);
    return base.filter((key) => availableKeys.has(key));
  }, [curvesWithColor, visibleCurveKeys]);

  const visibleCurves = useMemo(
    () => curvesWithColor.filter((curve) => effectiveVisibleKeys.includes(curve.key)),
    [curvesWithColor, effectiveVisibleKeys],
  );

  const chartData = useMemo(() => {
    const xSet = new Set<number>();
    const valueMaps = visibleCurves.map((curve) => {
      const valueMap = new Map<number, number>();
      const minId = curve.points.length > 0 ? Math.min(...curve.points.map((point) => point.id)) : 0;
      curve.points.forEach((point) => {
        let xValue: number | null = null;
        if (xAxisMode === "normalized") {
          if (curve.xIsNsTimeline) {
            xValue = Number(((point.id - minId) / 1_000_000_000).toFixed(6));
          } else if (curve.frequencyHz && curve.frequencyHz > 0) {
            xValue = Number(((point.id - minId) / curve.frequencyHz).toFixed(4));
          } else {
            xValue = point.id - minId;
          }
        } else {
          xValue = curve.xIsNsTimeline ? Number((point.id / 1_000_000_000).toFixed(6)) : point.id;
        }
        if (xValue == null) {
          return;
        }
        xSet.add(xValue);
        valueMap.set(xValue, point.rttMs);
      });
      return valueMap;
    });

    return [...xSet]
      .sort((left, right) => left - right)
      .map((xValue) => {
        const row: Record<string, number | null> = { x: xValue };
        visibleCurves.forEach((curve, index) => {
          row[curve.key] = valueMaps[index].get(xValue) ?? null;
        });
        return row;
      });
  }, [visibleCurves, xAxisMode]);

  const hasNsTimeline = useMemo(
    () => visibleCurves.some((curve) => curve.xIsNsTimeline),
    [visibleCurves],
  );

  return (
    <>
      <Paper sx={{ p: 1.5, width: "100%", minWidth: 0, maxWidth: "100%" }}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          spacing={1}
          sx={{ mb: 1, minWidth: 0 }}
        >
          <Typography variant="subtitle2">{title}</Typography>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={xAxisMode}
            onChange={(_event, nextMode: "raw" | "normalized" | null) => {
              if (nextMode) {
                setXAxisMode(nextMode);
              }
            }}
          >
            <ToggleButton value="raw">Raw</ToggleButton>
            <ToggleButton value="normalized">Normalized</ToggleButton>
          </ToggleButtonGroup>
        </Stack>
        <Box sx={{ width: "100%", minWidth: 0, maxWidth: "100%", overflowX: "hidden", height: 420 }}>
          <ResponsiveContainer>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="x"
                type="number"
                tick={{ fontSize: 11 }}
                domain={["dataMin", "dataMax"]}
                tickFormatter={(value) =>
                  xAxisMode === "normalized"
                    ? `${Number(value).toFixed(2)}s`
                    : hasNsTimeline
                      ? `${Number(value).toFixed(2)}s`
                      : String(Math.round(Number(value)))
                }
                label={{
                  value:
                    xAxisMode === "normalized"
                      ? "Time (s, start at 0)"
                      : hasNsTimeline
                        ? "Time (s)"
                        : "Sample id",
                  position: "insideBottom",
                  offset: -4,
                }}
              />
              <YAxis tick={{ fontSize: 11 }} label={{ value: "RTT (ms)", angle: -90, position: "insideLeft" }} />
              <Tooltip
                formatter={(value) => formatTooltipValue(value)}
                labelFormatter={(value) =>
                  xAxisMode === "normalized"
                    ? `t: ${Number(value).toFixed(4)} s`
                    : hasNsTimeline
                      ? `t: ${Number(value).toFixed(4)} s`
                      : `id: ${Math.round(Number(value))}`
                }
              />
              {visibleCurves.map((curve) => (
                <Line
                  key={curve.key}
                  type="monotone"
                  dataKey={curve.key}
                  name={curve.label}
                  stroke={curve.color}
                  strokeWidth={1.8}
                  dot={false}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </Box>
      </Paper>

      <Paper sx={{ p: 1.5, width: "100%", minWidth: 0, maxWidth: "100%" }}>
        <Typography variant="subtitle2" gutterBottom>
          Curve Display
        </Typography>
        <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setVisibleCurveKeys(curvesWithColor.map((curve) => curve.key))}
          >
            Show All
          </Button>
          <Button size="small" variant="outlined" onClick={() => setVisibleCurveKeys([])}>
            Hide All
          </Button>
        </Stack>
        <Stack spacing={0.8}>
          {curvesWithColor.map((curve) => (
            <Box
              key={`label-${curve.key}`}
              sx={{
                display: "grid",
                gridTemplateColumns: { xs: "auto 18px minmax(0,1fr)", md: "auto 18px minmax(0,1fr) auto" },
                gap: 1,
                alignItems: "center",
                minWidth: 0,
              }}
            >
              <FormControlLabel
                control={
                  <Checkbox
                    size="small"
                    checked={effectiveVisibleKeys.includes(curve.key)}
                    onChange={(_event, checked) => {
                      setVisibleCurveKeys((prev) => {
                        const base = prev ?? curvesWithColor.map((item) => item.key);
                        if (checked) {
                          if (base.includes(curve.key)) {
                            return base;
                          }
                          return [...base, curve.key];
                        }
                        return base.filter((key) => key !== curve.key);
                      });
                    }}
                  />
                }
                label=""
                sx={{ mr: 0 }}
              />
              <Box sx={{ width: 14, height: 14, borderRadius: "3px", backgroundColor: curve.color }} />
              <Typography
                variant="body2"
                sx={{ minWidth: 0, overflowWrap: "anywhere", wordBreak: "break-word" }}
              >
                {curve.label}
              </Typography>
              {buildRunLink ? (
                <Stack
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  sx={{ gridColumn: { xs: "1 / -1", md: "auto" }, justifySelf: { xs: "flex-start", md: "flex-end" } }}
                >
                  <Button
                    variant={selectedRunIds.includes(curve.runNumber) ? "contained" : "outlined"}
                    size="small"
                    startIcon={<CompareArrowsIcon fontSize="small" />}
                    onClick={() => toggleRunInComparison(curve.runNumber)}
                  >
                    {selectedRunIds.includes(curve.runNumber) ? "In Comparison" : "Compare"}
                  </Button>
                  <Button
                    component={RouterLink}
                    to={buildRunLink(curve.runNumber)}
                    variant="text"
                    size="small"
                    endIcon={<OpenInNewIcon fontSize="small" />}
                  >
                    Open
                  </Button>
                </Stack>
              ) : (
                <Box />
              )}
            </Box>
          ))}
        </Stack>
      </Paper>
    </>
  );
}
