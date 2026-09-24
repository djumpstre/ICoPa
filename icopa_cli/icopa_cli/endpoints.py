
class Endpoints:
    """ICoPa Hub API endpoints."""

    # Auth
    LOGIN = "auth/user_login/"
    LOGOUT = "auth/user_logout/"

    # Inventory
    INVENTORY = "inventory/"
    INVENTORY_VM = "inventory/vm/"
    INVENTORY_CHECK_VM = "inventory/check_vm/"
    INVENTORY_STOP_CONTAINER = "inventory/stop_container/"

    # Runtime environment
    RUNTIME_ENV = "runtime_env/"
    RUNTIME_ENV_DETAIL = "runtime_env/env/"
    RUNTIME_ENV_ACTIONS = "runtime_env/actions/"

    # Scenario
    SCENARIOS = "scenarios/"
    SCENARIO_DETAIL = "scenarios/scene/"
    SCENARIO_VALIDATE = "scenarios/scene/"

    # Profiling experiments
    PROFILING_EXPS = "profiling_exps/"
    PROFILING_EXP_DETAIL = "profiling_exps/exp/"
    PROFILING_EXP_START = "profiling_exps/exp/"
    PROFILING_EXP_GENERATE = "profiling_exps/exp/"
    PROFILING_EXP_RUN_CHECK = "profiling_exps/exp/"
    PROFILING_EXP_RUNS = "profiling_exps/exp/"
    PROFILING_RUNS = "profiling_exps/runs/"
    PROFILING_RUN_DETAIL = "profiling_exps/runs/"

    # Cloud provisioning
    CLOUD_VMS = "cloud_provisioning/vms/"
    CLOUD_VM_UPLOAD = "cloud_provisioning/vms/upload/"
    CLOUD_VM_CHECK_DETAIL = "cloud_provisioning/vms/{vm_id}/check_requests/{check_id}/"
    CLOUD_VM_START_DETAIL = "cloud_provisioning/vms/{vm_id}/start_requests/{start_id}/"
    CLOUD_VM_STOP_DETAIL = "cloud_provisioning/vms/{vm_id}/stop_requests/{stop_id}/"
