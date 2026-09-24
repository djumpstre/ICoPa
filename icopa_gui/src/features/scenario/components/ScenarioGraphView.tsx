import { Box, Paper, Stack, Typography } from "@mui/material";
import { useEffect, useMemo } from "react";

import { formatRelativeSinceNow } from "../../../shared/utils/date";
import { buildScenarioGraphLayout } from "../../../shared/utils/graph";
import { useInventoryStore } from "../../../stores/inventory.store";
import type { Scenario } from "../types";

interface ScenarioGraphViewProps {
  scenario: Scenario;
}

function extractNodeName(node: Record<string, unknown>): string {
  const nodeName = node.nodeName;
  const name = node.name;
  if (typeof nodeName === "string" && nodeName.trim()) {
    return nodeName;
  }
  if (typeof name === "string" && name.trim()) {
    return name;
  }
  return "unknown-node";
}

function extractNodeIp(node: Record<string, unknown>): string | undefined {
  const candidate = node.ipAddress ?? node.ip ?? node.address ?? node.host;
  if (typeof candidate === "string" && candidate.trim()) {
    return candidate;
  }
  return undefined;
}

function extractNodeRef(node: Record<string, unknown>): string | undefined {
  const ref = node.ref;
  if (typeof ref === "string" && ref.trim()) {
    return ref;
  }
  return undefined;
}

function extractManagedContainerNames(raw: unknown): string[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const names: string[] = [];
  for (const item of raw) {
    if (typeof item !== "string") {
      continue;
    }
    const text = item.trim();
    if (text && !names.includes(text)) {
      names.push(text);
    }
  }
  return names;
}

interface ScenarioNodeSummary {
  nodeName: string;
  ipAddress: string;
  vmNameForDetail: string | null;
  managedContainers: string[];
}

interface ScenarioEdgeSummary {
  source: string;
  target: string;
  name: string;
  linkType: string;
  type: string;
}

interface NodeCheckResult {
  ok: boolean | null;
}

interface EdgeCheckResult {
  ok: boolean | null;
  latencyAvgMs: number | null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is Record<string, unknown> => Boolean(asRecord(item)));
}

function extractEdgeLabelType(edge: Record<string, unknown>): string {
  const linkValue = edge.link;
  if (typeof linkValue === "string" && linkValue.trim()) {
    return linkValue.trim();
  }
  if (Array.isArray(linkValue)) {
    const linkLabels = linkValue
      .map((item) => (typeof item === "string" ? item.trim() : ""))
      .filter((item) => Boolean(item));
    if (linkLabels.length > 0) {
      return linkLabels.join("/");
    }
  }
  return "";
}

function extractEdgeSummary(edge: Record<string, unknown>, index: number): ScenarioEdgeSummary | null {
  const source = typeof edge.from === "string" ? edge.from.trim() : "";
  const target = typeof edge.to === "string" ? edge.to.trim() : "";
  if (!source || !target) {
    return null;
  }
  const nameValue = typeof edge.name === "string" ? edge.name.trim() : "";
  const typeValue = typeof edge.type === "string" ? edge.type.trim() : "";
  const linkType = extractEdgeLabelType(edge);
  return {
    source,
    target,
    name: nameValue || `edge-${index + 1}`,
    linkType,
    type: typeValue,
  };
}

function getEdgeDetailLabel(edge: ScenarioEdgeSummary): string {
  const labels: string[] = [];
  if (edge.linkType) {
    labels.push(`link=${edge.linkType}`);
  }
  if (edge.type) {
    labels.push(`type=${edge.type}`);
  }
  return labels.join(" | ");
}

function extractBoolean(value: unknown): boolean | null {
  if (typeof value === "boolean") {
    return value;
  }
  return null;
}

function extractNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

function extractString(value: unknown): string {
  if (typeof value === "string") {
    return value.trim();
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

function nodeStatusLabel(ok: boolean | null, checkedAt: string): string {
  const relative = formatRelativeSinceNow(checkedAt) || "before <1 min";
  if (ok === true) {
    return `Reachable, ${relative}`;
  }
  if (ok === false) {
    return `Unreachable, ${relative}`;
  }
  return "";
}

function edgeStatusLabel(result: EdgeCheckResult | undefined, checkedAt: string): string {
  if (!result) {
    return "";
  }
  const relative = formatRelativeSinceNow(checkedAt) || "before <1 min";
  if (result.ok === true && result.latencyAvgMs != null) {
    return `Ping ${result.latencyAvgMs.toFixed(1)} ms, ${relative}`;
  }
  if (result.ok === true) {
    return `Connected, ${relative}`;
  }
  if (result.ok === false) {
    return `Ping failed, ${relative}`;
  }
  return "";
}

export function ScenarioGraphView({ scenario }: ScenarioGraphViewProps) {
  const inventoryList = useInventoryStore((state) => state.list);
  const fetchInventoryList = useInventoryStore((state) => state.fetchList);

  useEffect(() => {
    if (inventoryList.length === 0) {
      void fetchInventoryList();
    }
  }, [fetchInventoryList, inventoryList.length]);

  const rawNodes = useMemo(
    () =>
      (scenario.nodes ?? []).filter(
        (node): node is Record<string, unknown> => typeof node === "object" && node !== null,
      ),
    [scenario.nodes],
  );

  const nodes = rawNodes.map((node) => extractNodeName(node));

  const rawEdges = useMemo<Record<string, unknown>[]>(() => {
    if (Array.isArray(scenario.graph_edges) && scenario.graph_edges.length > 0) {
      return scenario.graph_edges.map((edge) => ({
        from: edge.from,
        to: edge.to,
        name: edge.name,
        type: edge.type,
        link: edge.link,
      }));
    }
    return (scenario.graph?.edges ?? []).filter(
      (edge): edge is Record<string, unknown> => typeof edge === "object" && edge !== null,
    );
  }, [scenario.graph?.edges, scenario.graph_edges]);

  const edges = useMemo(
    () =>
      rawEdges
        .map((edge, index) => extractEdgeSummary(edge, index))
        .filter((edge): edge is ScenarioEdgeSummary => Boolean(edge)),
    [rawEdges],
  );

  const inventoryByName = useMemo(() => {
    return new Map(inventoryList.map((vm) => [vm.name, vm]));
  }, [inventoryList]);

  const nodeSummaries = useMemo(() => {
    const summaries = new Map<string, ScenarioNodeSummary>();
    for (const node of rawNodes) {
      const nodeName = extractNodeName(node);
      const refName = extractNodeRef(node);
      const directIp = extractNodeIp(node);
      const candidates = Array.from(new Set([refName, nodeName].filter((value): value is string => Boolean(value))));
      const matchedVm = candidates.map((candidate) => inventoryByName.get(candidate)).find((vm) => Boolean(vm));
      // Show only ICoPa-managed containers from inventory VM state.
      const managedContainers = extractManagedContainerNames(matchedVm?.managed_containers);
      summaries.set(nodeName, {
        nodeName,
        ipAddress: directIp ?? matchedVm?.address ?? "-",
        vmNameForDetail: matchedVm?.name ?? null,
        managedContainers,
      });
    }
    return Array.from(summaries.values());
  }, [inventoryByName, rawNodes]);

  const nodeSummaryByName = useMemo(
    () => new Map(nodeSummaries.map((summary) => [summary.nodeName, summary])),
    [nodeSummaries],
  );

  const nodeValidationCheckedAt = useMemo(() => {
    const summary = asRecord(scenario.last_node_check_summary);
    const checkedAt = extractString(summary?.checked_at);
    return checkedAt || scenario.last_validation_at || "";
  }, [scenario.last_node_check_summary, scenario.last_validation_at]);

  const validationCheckedAt = scenario.last_validation_at ?? "";

  const nodeChecksByName = useMemo(() => {
    const map = new Map<string, NodeCheckResult>();
    const nodeSummary = asRecord(scenario.last_node_check_summary);
    const summaryRows = asRecordArray(nodeSummary?.results);
    const lastValidation = asRecord(scenario.last_validation_data);
    const checks = asRecord(lastValidation?.checks);
    const nodesCheck = asRecord(checks?.nodes);
    const rows = summaryRows.length > 0 ? summaryRows : asRecordArray(nodesCheck?.results);
    for (const row of rows) {
      const nodeNameRaw = row.node;
      const nodeName = typeof nodeNameRaw === "string" ? nodeNameRaw.trim() : "";
      if (!nodeName) {
        continue;
      }
      map.set(nodeName, { ok: extractBoolean(row.ok) });
    }
    return map;
  }, [scenario.last_node_check_summary, scenario.last_validation_data]);

  const edgeChecksByName = useMemo(() => {
    const map = new Map<string, EdgeCheckResult>();
    const graphSummary = asRecord(scenario.last_graph_check_summary);
    const rows = asRecordArray(graphSummary?.results);
    for (const row of rows) {
      const edgeNameRaw = row.edge_name;
      const edgeName = typeof edgeNameRaw === "string" ? edgeNameRaw.trim() : "";
      if (!edgeName) {
        continue;
      }
      const metrics = asRecord(row.metrics);
      map.set(edgeName, {
        ok: extractBoolean(row.ok),
        latencyAvgMs: extractNumber(metrics?.latency_avg_ms),
      });
    }
    return map;
  }, [scenario.last_graph_check_summary]);

  const layout = buildScenarioGraphLayout(
    nodes,
    edges.map((edge) => ({ source: edge.source, target: edge.target })),
  );

  return (
    <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems="stretch" sx={{ width: "100%", minWidth: 0 }}>
      <Paper sx={{ p: 2, overflowX: "auto", flex: 1.8, minWidth: 0 }}>
        <Typography variant="h6" gutterBottom>
          Compute Node Graph
        </Typography>
        <svg
          data-testid="scenario-graph"
          viewBox={`0 0 ${layout.size.width} ${layout.size.height}`}
          style={{ width: "100%", height: "auto", minWidth: 0, background: "rgba(8,18,26,0.6)", borderRadius: 8 }}
        >
          <defs>
            <marker id="arrow" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
              <polygon points="0 0, 10 3.5, 0 7" fill="#74d8c9" />
            </marker>
          </defs>
          {edges.map((edge, index) => {
            const source = layout.nodes.find((node) => node.id === edge.source);
            const target = layout.nodes.find((node) => node.id === edge.target);
            if (!source || !target) {
              return null;
            }
            const labelX = (source.x + target.x) / 2;
            const labelY = (source.y + target.y) / 2 - 8;
            const edgeCheck = edgeChecksByName.get(edge.name);
            const latencyLabel =
              edgeCheck?.ok === true && edgeCheck.latencyAvgMs != null ? `${edgeCheck.latencyAvgMs.toFixed(1)} ms` : "";
            const edgeDetail = [getEdgeDetailLabel(edge), latencyLabel].filter((part) => Boolean(part)).join(" | ");
            return (
              <g key={`${edge.source}->${edge.target}-${edge.name}-${index}`}>
                <line
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke="#74d8c9"
                  strokeWidth="2"
                  markerEnd="url(#arrow)"
                />
                <text
                  x={labelX}
                  y={labelY}
                  textAnchor="middle"
                  fontSize="10"
                  fill="#f4fbff"
                  stroke="rgba(8,18,26,0.9)"
                  strokeWidth="3"
                  paintOrder="stroke"
                >
                  {edge.name}
                </text>
                {edgeDetail ? (
                  <text
                    x={labelX}
                    y={labelY + 12}
                    textAnchor="middle"
                    fontSize="9"
                    fill="#e1eff7"
                    stroke="rgba(8,18,26,0.9)"
                    strokeWidth="3"
                    paintOrder="stroke"
                  >
                    {edgeDetail}
                  </text>
                ) : null}
              </g>
            );
          })}
          {layout.nodes.map((node) => {
            const summary = nodeSummaryByName.get(node.id);
            const vmNameForDetail = summary?.vmNameForDetail;
            const graphNode = (
              <g key={node.id}>
                <circle cx={node.x} cy={node.y} r="26" fill="#1b3c4d" stroke="#74d8c9" strokeWidth="2" />
                <text
                  x={node.x}
                  y={node.y + 5}
                  textAnchor="middle"
                  fontSize="11"
                  fill="#e9f2f6"
                  style={{ pointerEvents: "none" }}
                >
                  {node.label}
                </text>
              </g>
            );
            if (!vmNameForDetail) {
              return graphNode;
            }
            return (
              <a key={node.id} href={`/inventory/${encodeURIComponent(vmNameForDetail)}`}>
                {graphNode}
              </a>
            );
          })}
        </svg>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Nodes: {layout.nodes.length} | Edges: {edges.length}
        </Typography>
      </Paper>

      <Stack spacing={2} sx={{ flex: 1, minWidth: 0 }}>
        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            Nodes
          </Typography>
          <Stack spacing={1}>
            {nodeSummaries.map((node) => (
              <Box
                key={node.nodeName}
                sx={{
                  p: 1.2,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1.5,
                }}
              >
                {node.vmNameForDetail ? (
                  <Typography
                    component="a"
                    href={`/inventory/${encodeURIComponent(node.vmNameForDetail)}`}
                    sx={{ color: "primary.main", textDecoration: "none", fontWeight: 600 }}
                  >
                    {node.nodeName}
                  </Typography>
                ) : (
                  <Typography sx={{ fontWeight: 600 }}>{node.nodeName}</Typography>
                )}
                <Typography variant="body2" color="text.secondary">
                  {node.ipAddress}
                </Typography>
                <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.6 }}>
                  Managed container: {node.managedContainers.length > 0 ? node.managedContainers.join(", ") : "-"}
                </Typography>
                {nodeValidationCheckedAt && nodeChecksByName.has(node.nodeName) ? (
                  (() => {
                    const label = nodeStatusLabel(nodeChecksByName.get(node.nodeName)?.ok ?? null, nodeValidationCheckedAt);
                    return label ? (
                      <Typography variant="caption" color="text.secondary">
                        {label}
                      </Typography>
                    ) : null;
                  })()
                ) : null}
              </Box>
            ))}
            {nodeSummaries.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No nodes
              </Typography>
            )}
          </Stack>
        </Paper>

        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            Edges
          </Typography>
          <Stack spacing={0.8}>
            {edges.map((edge, index) => (
              <Box
                key={`${edge.source}-${edge.target}-${edge.name}-${index}`}
                sx={{
                  p: 1,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1.25,
                }}
              >
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {edge.name}: {edge.source} -&gt; {edge.target}
                </Typography>
                {getEdgeDetailLabel(edge) ? (
                  <Typography variant="caption" color="text.secondary">
                    {getEdgeDetailLabel(edge)}
                  </Typography>
                ) : null}
                {validationCheckedAt
                  ? (() => {
                      const label = edgeStatusLabel(edgeChecksByName.get(edge.name), validationCheckedAt);
                      return label ? (
                        <Typography variant="caption" color="text.secondary" display="block">
                          {label}
                        </Typography>
                      ) : null;
                    })()
                  : null}
              </Box>
            ))}
            {edges.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No edges
              </Typography>
            )}
          </Stack>
        </Paper>
      </Stack>
    </Stack>
  );
}
