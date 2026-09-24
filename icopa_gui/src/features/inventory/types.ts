export interface InventoryCredential {
  id?: number;
  name?: string;
  key_path?: string;
  user_name?: string;
}

export interface InventoryContainerStatus {
  name?: string;
  state?: string;
  status?: string;
  id?: string;
  image?: string;
  command?: string;
  created_at?: string;
  running_for?: string;
  ports?: string;
}

export interface InventoryVm {
  id?: number;
  name: string;
  group_name?: string;
  address?: string;
  user_name?: string;
  port?: number;
  status?: string;
  metadata?: Record<string, unknown>;
  networking?: Record<string, unknown>;
  system_info?: Record<string, unknown>;
  managed_containers?: string[];
  last_connection_time?: string;
  created_at?: string;
  updated_at?: string;
  credential?: InventoryCredential | null;
}

export interface InventoryVmCheckResult {
  name: string;
  address?: string;
  port?: number;
  ok: boolean;
  returncode?: number;
  stdout?: string;
  stderr?: string;
  system_info?: Record<string, unknown>;
  managed_containers?: string[];
  started_containers?: string[];
  managed_container_status?: InventoryContainerStatus[];
}

export interface InventoryVmStopContainerResult {
  ok: boolean;
  status?: string;
  state: string;
  message: string;
  task_id?: string;
  vm_id?: number;
  vm?: string;
  container?: string;
  error?: string;
  managed_containers?: string[];
  managed_container_status?: InventoryContainerStatus[];
}
