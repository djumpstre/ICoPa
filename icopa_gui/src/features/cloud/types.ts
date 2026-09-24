export interface CloudProvisioningEvent {
  id: number;
  level: string;
  message: string;
  event_data: Record<string, unknown>;
  created_at: string;
}

export interface CloudCheckRequest {
  id: number;
  status: string;
  task_id: string;
  check_data: Record<string, unknown>;
  last_error: string;
  created_at: string;
  updated_at: string;
}

export interface CloudStopRequest {
  id: number;
  status: string;
  task_id: string;
  stop_data: Record<string, unknown>;
  last_error: string;
  created_at: string;
  updated_at: string;
}

export interface CloudStartRequest {
  id: number;
  status: string;
  task_id: string;
  start_data: Record<string, unknown>;
  last_error: string;
  created_at: string;
  updated_at: string;
}

export interface CloudInventoryVmRef {
  id: number;
  name: string;
  address: string;
  user_name: string;
  status: string;
  archived: boolean;
}

export interface CloudProvisioningVm {
  id: number;
  name: string;
  group_name: string;
  provider: string;
  instance_provider_name: string;
  instance_location: string;
  instance_size: string;
  instance_power_state: string;
  instance_public_ip: string;
  instance_nic_name: string;
  status: string;
  task_id: string;
  last_error: string;
  raw_template: Record<string, unknown>;
  request_spec: Record<string, unknown>;
  result_data: Record<string, unknown>;
  inventory_vm: CloudInventoryVmRef | null;
  events: CloudProvisioningEvent[];
  check_requests: CloudCheckRequest[];
  stop_requests: CloudStopRequest[];
  start_requests: CloudStartRequest[];
  created_at: string;
  updated_at: string;
}

export interface CloudCheckDispatchResponse {
  provisioning_vm_id: number;
  check_request_id: number;
  status: string;
  task_id: string;
}

export interface CloudUploadResponse {
  provider: string;
  total_created: number;
  vms: CloudProvisioningVm[];
}

export interface CloudStopDispatchResponse {
  provisioning_vm_id: number;
  stop_request_id: number;
  status: string;
  task_id: string;
}

export interface CloudStartDispatchResponse {
  provisioning_vm_id: number;
  start_request_id: number;
  status: string;
  task_id: string;
}
