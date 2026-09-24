export interface ScenarioGraphLayoutNode {
  id: string;
  label: string;
  x: number;
  y: number;
}

export interface ScenarioGraphLayoutEdge {
  source: string;
  target: string;
}

export function buildScenarioGraphLayout(nodes: string[], edges: ScenarioGraphLayoutEdge[]) {
  const uniqueNodes = Array.from(new Set(nodes.filter((node) => node.trim().length > 0)));
  const radius = Math.max(140, uniqueNodes.length * 16);
  const centerX = 220;
  const centerY = 180;
  const layoutNodes: ScenarioGraphLayoutNode[] = uniqueNodes.map((node, index) => {
    const angle = (2 * Math.PI * index) / Math.max(uniqueNodes.length, 1);
    return {
      id: node,
      label: node,
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
    };
  });

  return {
    nodes: layoutNodes,
    edges,
    size: { width: 460, height: 360 },
  };
}
