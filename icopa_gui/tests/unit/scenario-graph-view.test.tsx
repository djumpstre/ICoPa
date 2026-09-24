import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScenarioGraphView } from "../../src/features/scenario/components/ScenarioGraphView";
import { useInventoryStore } from "../../src/stores/inventory.store";

describe("ScenarioGraphView", () => {
  it("renders graph metadata for nodes and edges", () => {
    useInventoryStore.setState((state) => ({
      ...state,
      list: [
        {
          name: "vm_home",
          address: "192.168.1.100",
          managed_containers: ["icopa-zenoh-probe-sender", "icopa-zenoh-router"],
        },
      ],
    }));

    render(
      <ScenarioGraphView
        scenario={{
          name: "example",
          last_validation_at: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
          last_validation_data: {
            checks: {
              nodes: {
                results: [{ node: "vm_home", ok: true }],
              },
            },
          },
          last_graph_check_summary: {
            results: [{ edge_name: "robot_to_cloud", ok: true, metrics: { latency_avg_ms: 12.3 } }],
          },
          nodes: [{ nodeName: "vm_home" }, { nodeName: "cloud_vm_bw" }],
          graph: {
            edges: [
              {
                name: "robot_to_cloud",
                from: "vm_home",
                to: "cloud_vm_bw",
                link: ["wan", "wifi"],
              },
            ],
          },
        }}
      />,
    );

    expect(screen.getByText(/Nodes: 2 \| Edges: 1/)).toBeInTheDocument();
    expect(screen.getByText("robot_to_cloud")).toBeInTheDocument();
    expect(screen.getAllByText("link=wan/wifi").length).toBeGreaterThan(0);
    expect(screen.getByText(/Reachable/)).toBeInTheDocument();
    expect(screen.getByText(/Ping 12.3 ms/)).toBeInTheDocument();
    expect(screen.getByText(/Managed container: icopa-zenoh-probe-sender, icopa-zenoh-router/)).toBeInTheDocument();
    expect(screen.getByText(/Managed container: -/)).toBeInTheDocument();
    expect(screen.getByTestId("scenario-graph")).toBeInTheDocument();
  });
});
