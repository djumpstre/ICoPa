import { configureSessionLifecycle } from "../core/api/session";
import { useAuthStore } from "../stores/auth.store";
import { useCloudStore } from "../stores/cloud.store";
import { useExperimentStore } from "../stores/experiment.store";
import { useInventoryStore } from "../stores/inventory.store";
import { useRunComparisonStore } from "../stores/runComparison.store";
import { useRuntimeStore } from "../stores/runtime.store";
import { useScenarioStore } from "../stores/scenario.store";
import { useUiStore } from "../stores/ui.store";

function clearUserData() {
  useCloudStore.getState().clear();
  useExperimentStore.getState().clear();
  useInventoryStore.getState().clear();
  useRunComparisonStore.getState().clear();
  useRuntimeStore.getState().clear();
  useScenarioStore.getState().clear();
  useUiStore.getState().clearAccountPreferences();
}

export function initializeSessionLifecycle() {
  let revision = 0;
  // Comparison selections were persisted without an account identity.
  clearUserData();
  const unsubscribe = useAuthStore.subscribe((state, previous) => {
    if (state.token !== previous.token) {
      revision += 1;
      clearUserData();
    }
  });
  const disconnect = configureSessionLifecycle({
    token: () => useAuthStore.getState().token,
    revision: () => revision,
    expire: () => useAuthStore.getState().clearSession(),
  });
  return () => {
    unsubscribe();
    disconnect();
  };
}
