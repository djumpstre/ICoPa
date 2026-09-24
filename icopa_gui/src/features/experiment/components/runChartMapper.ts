import { ChartDatum, ProfilingGeneratedRun } from "../types";

interface TrackerPhase {
  name?: string;
  status?: string;
}

interface TrackerRun {
  phases?: TrackerPhase[];
}

interface TrackerShape {
  runs?: Record<string, TrackerRun>;
  events?: Array<Record<string, unknown>>;
}

function asTracker(tracker: unknown): TrackerShape {
  if (!tracker || typeof tracker !== "object") {
    return {};
  }
  return tracker as TrackerShape;
}

export function buildRunCounterChart(run: ProfilingGeneratedRun): ChartDatum[] {
  return [
    { label: "Completed", value: Number(run.completed_runs ?? 0) },
    { label: "Failed", value: Number(run.failed_runs ?? 0) },
    { label: "Remaining", value: Math.max(Number(run.total_runs ?? 0) - Number(run.completed_runs ?? 0), 0) },
  ];
}

export function buildPhaseStatusChart(run: ProfilingGeneratedRun): ChartDatum[] {
  const tracker = asTracker(run.phase_action_execution_status);
  const counts: Record<string, number> = {};

  Object.values(tracker.runs ?? {}).forEach((runEntry) => {
    (runEntry.phases ?? []).forEach((phase) => {
      const status = String(phase.status ?? "UNKNOWN").toUpperCase();
      counts[status] = (counts[status] ?? 0) + 1;
    });
  });

  if (Object.keys(counts).length === 0) {
    return [{ label: "UNKNOWN", value: 0 }];
  }

  return Object.entries(counts).map(([label, value]) => ({ label, value }));
}

export function buildEventTimeline(run: ProfilingGeneratedRun): Array<{ step: number; events: number }> {
  const tracker = asTracker(run.phase_action_execution_status);
  const events = Array.isArray(tracker.events) ? tracker.events : [];

  return events.map((_, index) => ({
    step: index + 1,
    events: index + 1,
  }));
}
