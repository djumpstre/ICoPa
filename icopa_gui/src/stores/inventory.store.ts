import { create } from "zustand";

import { endpoints } from "../core/api/endpoints";
import { ApiError, toErrorMessage } from "../core/api/errors";
import { requestJson } from "../core/api/http";
import { InventoryVm, InventoryVmCheckResult, InventoryVmStopContainerResult } from "../features/inventory/types";
import { useAuthStore } from "./auth.store";

interface InventoryStore {
  list: InventoryVm[];
  detailByName: Record<string, InventoryVm>;
  deletingByName: Record<string, boolean>;
  deleteErrorByName: Record<string, string | undefined>;
  checkingByName: Record<string, boolean>;
  checkResultByName: Record<string, InventoryVmCheckResult | undefined>;
  checkErrorByName: Record<string, string | undefined>;
  stoppingContainerByKey: Record<string, boolean>;
  stopContainerErrorByKey: Record<string, string | undefined>;
  stopContainerResultByKey: Record<string, InventoryVmStopContainerResult | undefined>;
  loadingList: boolean;
  loadingDetail: boolean;
  error: string | null;
  lastFetchedAt: string | null;
  fetchList: () => Promise<void>;
  fetchDetail: (vmName: string) => Promise<void>;
  deleteVm: (vmName: string) => Promise<void>;
  checkVm: (vmName: string) => Promise<void>;
  stopContainer: (params: { vmName?: string; vmId?: number; containerName: string }) => Promise<void>;
  clear: () => void;
}

function getRequiredToken(): string {
  const token = useAuthStore.getState().token;
  if (!token) {
    throw new ApiError("Missing access token", 401);
  }
  return token;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export const useInventoryStore = create<InventoryStore>((set) => ({
  list: [],
  detailByName: {},
  deletingByName: {},
  deleteErrorByName: {},
  checkingByName: {},
  checkResultByName: {},
  checkErrorByName: {},
  stoppingContainerByKey: {},
  stopContainerErrorByKey: {},
  stopContainerResultByKey: {},
  loadingList: false,
  loadingDetail: false,
  error: null,
  lastFetchedAt: null,
  fetchList: async () => {
    set({ loadingList: true, error: null });
    try {
      const token = getRequiredToken();
      const list = await requestJson<InventoryVm[]>(endpoints.inventory.list, { token });
      set({ list, loadingList: false, lastFetchedAt: new Date().toISOString() });
    } catch (error) {
      set({ loadingList: false, error: toErrorMessage(error) });
    }
  },
  fetchDetail: async (vmName) => {
    set({ loadingDetail: true, error: null });
    try {
      const token = getRequiredToken();
      const detail = await requestJson<InventoryVm>(endpoints.inventory.detail(vmName), { token });
      set((state) => ({
        loadingDetail: false,
        detailByName: {
          ...state.detailByName,
          [vmName]: detail,
        },
      }));
    } catch (error) {
      set({ loadingDetail: false, error: toErrorMessage(error) });
    }
  },
  deleteVm: async (vmName) => {
    set((state) => ({
      deletingByName: {
        ...state.deletingByName,
        [vmName]: true,
      },
      deleteErrorByName: {
        ...state.deleteErrorByName,
        [vmName]: undefined,
      },
    }));
    try {
      const token = getRequiredToken();
      await requestJson<unknown>(endpoints.inventory.detail(vmName), {
        method: "DELETE",
        token,
      });
      set((state) => {
        const nextDetail = { ...state.detailByName };
        delete nextDetail[vmName];
        const nextDeleting = { ...state.deletingByName, [vmName]: false };
        const nextDeleteError = { ...state.deleteErrorByName, [vmName]: undefined };
        return {
          detailByName: nextDetail,
          deletingByName: nextDeleting,
          deleteErrorByName: nextDeleteError,
          list: state.list.filter((item) => item.name !== vmName),
        };
      });
    } catch (error) {
      set((state) => ({
        deletingByName: {
          ...state.deletingByName,
          [vmName]: false,
        },
        deleteErrorByName: {
          ...state.deleteErrorByName,
          [vmName]: toErrorMessage(error),
        },
      }));
    }
  },
  checkVm: async (vmName) => {
    set((state) => ({
      checkingByName: {
        ...state.checkingByName,
        [vmName]: true,
      },
      checkErrorByName: {
        ...state.checkErrorByName,
        [vmName]: undefined,
      },
    }));
    try {
      const token = getRequiredToken();
      const result = await requestJson<InventoryVmCheckResult>(endpoints.inventory.check, {
        method: "POST",
        token,
        body: { name: vmName },
      });
      const nextStatus = result.ok ? "ACTIVE" : "ERROR";
      set((state) => ({
        checkingByName: {
          ...state.checkingByName,
          [vmName]: false,
        },
        checkResultByName: {
          ...state.checkResultByName,
          [vmName]: result,
        },
        list: state.list.map((item) =>
          item.name === vmName
            ? {
                ...item,
                status: nextStatus,
                system_info: result.system_info ?? item.system_info,
                managed_containers: result.managed_containers ?? item.managed_containers,
                last_connection_time: new Date().toISOString(),
              }
            : item,
        ),
        detailByName: state.detailByName[vmName]
          ? {
              ...state.detailByName,
              [vmName]: {
                ...state.detailByName[vmName],
                status: nextStatus,
                system_info: result.system_info ?? state.detailByName[vmName].system_info,
                managed_containers: result.managed_containers ?? state.detailByName[vmName].managed_containers,
                last_connection_time: new Date().toISOString(),
              },
            }
          : state.detailByName,
      }));
    } catch (error) {
      set((state) => ({
        checkingByName: {
          ...state.checkingByName,
          [vmName]: false,
        },
        checkErrorByName: {
          ...state.checkErrorByName,
          [vmName]: toErrorMessage(error),
        },
      }));
    }
  },
  stopContainer: async ({ vmName, vmId, containerName }) => {
    const keyVmName = (vmName ?? "").trim();
    const key = `${keyVmName}::${containerName}`;
    set((state) => ({
      stoppingContainerByKey: {
        ...state.stoppingContainerByKey,
        [key]: true,
      },
      stopContainerErrorByKey: {
        ...state.stopContainerErrorByKey,
        [key]: undefined,
      },
      stopContainerResultByKey: {
        ...state.stopContainerResultByKey,
        [key]: undefined,
      },
    }));
    try {
      const token = getRequiredToken();
      let pollingTaskId = "";
      let finalResult: InventoryVmStopContainerResult | undefined;
      const maxPollAttempts = 120;

      for (let attempt = 0; attempt < maxPollAttempts; attempt += 1) {
        const payload: Record<string, unknown> = { container: containerName };
        if (typeof vmId === "number") {
          payload.vm_id = vmId;
        } else if (keyVmName) {
          payload.vm = keyVmName;
        } else {
          throw new ApiError("VM id or VM name is required to stop container.", 400);
        }
        if (pollingTaskId) {
          payload.task_id = pollingTaskId;
        }

        const result = await requestJson<InventoryVmStopContainerResult>(endpoints.inventory.stopContainer, {
          method: "POST",
          token,
          body: payload,
        });
        if (typeof result.task_id === "string" && result.task_id.trim()) {
          pollingTaskId = result.task_id.trim();
        }
        set((state) => ({
          stopContainerResultByKey: {
            ...state.stopContainerResultByKey,
            [key]: result,
          },
        }));

        const statusValue = String(result.status ?? "").toUpperCase();
        const running = statusValue === "RUNNING" || result.state === "running";
        if (running) {
          await delay(2000);
          continue;
        }
        finalResult = result;
        break;
      }

      if (!finalResult) {
        throw new ApiError("Timed out while waiting for container stop/remove.", 408);
      }

      const targetVmName = ((finalResult.vm ?? keyVmName) || "").trim();
      const nextManagedContainers = Array.isArray(finalResult.managed_containers)
        ? finalResult.managed_containers
        : undefined;
      set((state) => ({
        stoppingContainerByKey: {
          ...state.stoppingContainerByKey,
          [key]: false,
        },
        stopContainerResultByKey: {
          ...state.stopContainerResultByKey,
          [key]: finalResult,
        },
        list: state.list.map((item) =>
          item.name === targetVmName
            ? {
                ...item,
                managed_containers: nextManagedContainers ?? item.managed_containers,
                last_connection_time: new Date().toISOString(),
              }
            : item,
        ),
        detailByName:
          targetVmName && state.detailByName[targetVmName]
            ? {
                ...state.detailByName,
                [targetVmName]: {
                  ...state.detailByName[targetVmName],
                  managed_containers: nextManagedContainers ?? state.detailByName[targetVmName].managed_containers,
                  metadata: {
                    ...(state.detailByName[targetVmName].metadata ?? {}),
                    managed_container_status: finalResult.managed_container_status ?? [],
                    managed_containers_running: nextManagedContainers ?? [],
                  },
                  last_connection_time: new Date().toISOString(),
                },
              }
            : state.detailByName,
      }));
    } catch (error) {
      set((state) => ({
        stoppingContainerByKey: {
          ...state.stoppingContainerByKey,
          [key]: false,
        },
        stopContainerErrorByKey: {
          ...state.stopContainerErrorByKey,
          [key]: toErrorMessage(error),
        },
      }));
    }
  },
  clear: () => {
    set({
      list: [],
      detailByName: {},
      deletingByName: {},
      deleteErrorByName: {},
      checkingByName: {},
      checkResultByName: {},
      checkErrorByName: {},
      stoppingContainerByKey: {},
      stopContainerErrorByKey: {},
      stopContainerResultByKey: {},
      loadingList: false,
      loadingDetail: false,
      error: null,
      lastFetchedAt: null,
    });
  },
}));
