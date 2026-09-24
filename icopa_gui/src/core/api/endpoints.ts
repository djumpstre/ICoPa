export const endpoints = {
  auth: {
    login: "auth/user_login/",
    logout: "auth/user_logout/",
  },
  inventory: {
    list: "inventory/",
    detail: (vmName: string) => `inventory/vm/${encodeURIComponent(vmName)}/`,
    check: "inventory/check_vm/",
    stopContainer: "inventory/stop_container/",
  },
  runtime: {
    list: "runtime_env/",
    detail: (envName: string) => `runtime_env/env/${encodeURIComponent(envName)}/`,
  },
  scenario: {
    list: "scenarios/",
    detail: (scenarioName: string) => `scenarios/scene/${encodeURIComponent(scenarioName)}/`,
    validate: (scenarioName: string) => `scenarios/scene/${encodeURIComponent(scenarioName)}/validate/`,
  },
  experiment: {
    list: "profiling_exps/",
    detail: (expName: string) => `profiling_exps/exp/${encodeURIComponent(expName)}/`,
    generate: (expName: string) => `profiling_exps/exp/${encodeURIComponent(expName)}/generate/`,
    start: (expName: string) => `profiling_exps/exp/${encodeURIComponent(expName)}/start/`,
    runsByExperiment: (expName: string) => `profiling_exps/exp/${encodeURIComponent(expName)}/runs/`,
    runList: "profiling_exps/runs/",
    runDetail: (runId: number) => `profiling_exps/runs/${runId}/`,
    runRerun: (runId: number) => `profiling_exps/runs/${runId}/rerun/`,
    comparisionList: "profiling_exps/comparisions/",
  },
  cloud: {
    list: (provider: string) => `cloud_provisioning/vms/?provider=${encodeURIComponent(provider)}`,
    detail: (id: number) => `cloud_provisioning/vms/${id}/`,
    upload: "cloud_provisioning/vms/upload/",
    create: (id: number) => `cloud_provisioning/vms/${id}/create/`,
    delete: (id: number) => `cloud_provisioning/vms/${id}/delete/`,
    start: (id: number) => `cloud_provisioning/vms/${id}/start/`,
    stop: (id: number) => `cloud_provisioning/vms/${id}/stop/`,
    check: (id: number) => `cloud_provisioning/vms/${id}/check/`,
    checkDetail: (id: number, checkId: number) =>
      `cloud_provisioning/vms/${id}/check_requests/${checkId}/`,
    startDetail: (id: number, startId: number) =>
      `cloud_provisioning/vms/${id}/start_requests/${startId}/`,
    stopDetail: (id: number, stopId: number) =>
      `cloud_provisioning/vms/${id}/stop_requests/${stopId}/`,
  },
};
