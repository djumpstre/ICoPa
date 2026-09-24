export interface RuntimeEnvironment {
  id?: number;
  name: string;
  kind?: string;
  metadata?: Record<string, unknown>;
  images?: Record<string, unknown>;
  tags?: Record<string, unknown>;
  parameters?: Record<string, unknown>;
  command_preset?: Array<Record<string, unknown>>;
  command_groups?: Record<string, unknown>;
  raw_payload?: Record<string, unknown>;
  raw_yaml?: string;
  serializer_rrt_config_file?: string;
  serializer_rrt_config_url?: string;
  serializer_rrt_config_path?: string;
  serializer_rrt_config_raw_yaml?: string;
  serializer_rrt_config_json?: unknown;
  uploaded_version?: number;
  created_at?: string;
  updated_at?: string;
}
