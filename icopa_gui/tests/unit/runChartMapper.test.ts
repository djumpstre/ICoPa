import { describe, expect, it } from "vitest";

import {
  buildEventTimeline,
  buildPhaseStatusChart,
  buildRunCounterChart,
} from "../../src/features/experiment/components/runChartMapper";
import { ProfilingGeneratedRun } from "../../src/features/experiment/types";

describe("run chart mapper", () => {
  const run: ProfilingGeneratedRun = {
    id: 1,
    total_runs: 4,
    completed_runs: 2,
    failed_runs: 1,
    phase_action_execution_status: {
      runs: {
        "run-001": {
          phases: [
            { name: "preflight", status: "SUCCEEDED" },
            { name: "probe", status: "FAILED" },
          ],
        },
      },
      events: [{ event: "phase_started" }, { event: "phase_finished" }],
    },
  };

  it("maps counter chart data", () => {
    expect(buildRunCounterChart(run)).toEqual([
      { label: "Completed", value: 2 },
      { label: "Failed", value: 1 },
      { label: "Remaining", value: 2 },
    ]);
  });

  it("maps phase status chart data", () => {
    const mapped = buildPhaseStatusChart(run);
    expect(mapped).toEqual(
      expect.arrayContaining([
        { label: "SUCCEEDED", value: 1 },
        { label: "FAILED", value: 1 },
      ]),
    );
  });

  it("maps event timeline", () => {
    expect(buildEventTimeline(run)).toEqual([
      { step: 1, events: 1 },
      { step: 2, events: 2 },
    ]);
  });
});
