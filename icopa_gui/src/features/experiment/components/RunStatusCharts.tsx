import { Paper, Typography } from "@mui/material";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "../../../shared/components/EmptyState";
import type { ProfilingGeneratedRun } from "../types";
import { buildEventTimeline, buildPhaseStatusChart, buildRunCounterChart } from "./runChartMapper";

interface RunStatusChartsProps {
  run: ProfilingGeneratedRun;
}

export function RunStatusCharts({ run }: RunStatusChartsProps) {
  const counterData = buildRunCounterChart(run);
  const phaseData = buildPhaseStatusChart(run);
  const timelineData = buildEventTimeline(run);

  return (
    <>
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Run Counters
        </Typography>
        <div style={{ width: "100%", height: 280 }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie dataKey="value" data={counterData} nameKey="label" outerRadius={90} fill="#23b59c" label />
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </Paper>

      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Phase Status Breakdown
        </Typography>
        <div style={{ width: "100%", height: 280 }}>
          <ResponsiveContainer>
            <BarChart data={phaseData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
              <XAxis dataKey="label" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="value" fill="#f0b15a" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Paper>

      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Event Timeline
        </Typography>
        {timelineData.length === 0 ? (
          <EmptyState label="No tracker events available yet." />
        ) : (
          <div style={{ width: "100%", height: 280 }}>
            <ResponsiveContainer>
              <LineChart data={timelineData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis dataKey="step" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Line dataKey="events" stroke="#74d8c9" strokeWidth={3} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Paper>
    </>
  );
}
