export interface ProfilingExperiment {
  id?: number;
  name: string;
  kind?: string;
  description?: string;
  scenario_name?: string;
  execution?: Record<string, unknown>;
  selections?: Record<string, unknown>;
  phases?: Array<Record<string, unknown>>;
  status?: string;
  start_count?: number;
  last_started_at?: string;
  has_generated_plan?: boolean;
  generated_payload?: Record<string, unknown>;
  generated_yaml?: string;
  total_generated_runs?: number;
  current_gen_version?: number;
  generated_at?: string;
  run_check_status?: string;
  run_check_report?: Record<string, unknown>;
  run_checked_at?: string;
  execution_status?: Record<string, unknown>;
  topology_overview?: {
    nodes?: Array<Record<string, unknown>>;
    edges?: Array<Record<string, unknown>>;
    selected_edge_names?: string[];
    latest_graph_validation?: Record<string, unknown>;
  };
  created_at?: string;
  updated_at?: string;
}

export interface ProfilingGeneratedRun {
  id: number;
  gen_version?: number;
  plan_run_id?: string;
  experiment_name?: string;
  generated_plan_name?: string;
  status?: string;
  stop_on_failure?: boolean;
  total_runs?: number;
  completed_runs?: number;
  failed_runs?: number;
  run_metadata?: Record<string, unknown>;
  characterization_parameters?: Record<string, unknown>;
  compiled_payload_snapshot?: Record<string, unknown>;
  task_id?: string;
  results?: Array<Record<string, unknown>>;
  phase_action_execution_status?: Record<string, unknown>;
  rrt_config_execution_snapshot?: Record<string, unknown>;
  run_metric_files?: Array<Record<string, unknown>>;
  rrt_configs?: Array<Record<string, unknown>>;
  rrt_summaries?: Array<Record<string, unknown>>;
  collected_metrics_path?: string;
  collected_metrics_url?: string;
  metrics_collected?: boolean;
  note?: string;
  error_message?: string;
  started_at?: string;
  finished_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ProfilingComparisionRun {
  id?: number;
  run_id: number;
  deleted?: boolean;
  experiment_name?: string;
  plan_run_id?: string;
  status?: string;
  created_at?: string;
}

export interface ProfilingComparision {
  id?: number;
  name: string;
  description?: string;
  run_count?: number;
  runs?: ProfilingComparisionRun[];
  created_at?: string;
  updated_at?: string;
}

export interface ChartDatum {
  label: string;
  value: number;
}
