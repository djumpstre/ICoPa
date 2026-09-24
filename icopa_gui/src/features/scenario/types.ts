export interface ScenarioNode {
  name?: string;
  nodeName?: string;
  kind?: string;
  type?: string;
  [key: string]: unknown;
}

export interface ScenarioEdge {
  from?: string;
  to?: string;
  name?: string;
  type?: string;
  link?: string | string[];
  [key: string]: unknown;
}

export interface ScenarioGraph {
  edges?: ScenarioEdge[];
  [key: string]: unknown;
}

export interface ScenarioGraphEdgeSummary {
  name: string;
  from: string;
  to: string;
  type?: string;
  link?: string | string[];
}

export interface Scenario {
  id?: number;
  name: string;
  kind?: string;
  description?: string;
  metadata?: Record<string, unknown>;
  nodes?: ScenarioNode[];
  graph?: ScenarioGraph;
  graph_edges?: ScenarioGraphEdgeSummary[];
  graph_edge_count?: number;
  runtime_env?: Array<Record<string, unknown> | string>;
  payloads?: Array<Record<string, unknown>>;
  background_workloads?: Array<Record<string, unknown>>;
  check_status_node?: string;
  check_status_graph?: string;
  check_status_actions?: string;
  validation_status?: string;
  validation_requested?: boolean;
  last_validation_task_id?: string;
  last_validation_at?: string;
  last_validation_data?: Record<string, unknown>;
  last_node_check_summary?: Record<string, unknown>;
  last_graph_check_summary?: Record<string, unknown>;
  validation_history?: Array<Record<string, unknown>>;
  validation_trace?: Array<Record<string, unknown>>;
  raw_payload?: Record<string, unknown>;
  raw_yaml?: string;
  created_at?: string;
  updated_at?: string;
}
