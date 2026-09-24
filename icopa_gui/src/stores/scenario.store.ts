import { create } from "zustand";

import { endpoints } from "../core/api/endpoints";
import { ApiError, toErrorMessage } from "../core/api/errors";
import { requestJson } from "../core/api/http";
import { Scenario } from "../features/scenario/types";
import { useAuthStore } from "./auth.store";

interface ScenarioStore {
  list: Scenario[];
  detailByName: Record<string, Scenario>;
  loadingList: boolean;
  loadingDetail: boolean;
  error: string | null;
  lastFetchedAt: string | null;
  fetchList: () => Promise<void>;
  fetchDetail: (scenarioName: string) => Promise<void>;
  clear: () => void;
}

function getRequiredToken(): string {
  const token = useAuthStore.getState().token;
  if (!token) {
    throw new ApiError("Missing access token", 401);
  }
  return token;
}

export const useScenarioStore = create<ScenarioStore>((set) => ({
  list: [],
  detailByName: {},
  loadingList: false,
  loadingDetail: false,
  error: null,
  lastFetchedAt: null,
  fetchList: async () => {
    set({ loadingList: true, error: null });
    try {
      const token = getRequiredToken();
      const list = await requestJson<Scenario[]>(endpoints.scenario.list, { token });
      set({ list, loadingList: false, lastFetchedAt: new Date().toISOString() });
    } catch (error) {
      set({ loadingList: false, error: toErrorMessage(error) });
    }
  },
  fetchDetail: async (scenarioName) => {
    set({ loadingDetail: true, error: null });
    try {
      const token = getRequiredToken();
      const detail = await requestJson<Scenario>(endpoints.scenario.detail(scenarioName), { token });
      set((state) => ({
        loadingDetail: false,
        detailByName: {
          ...state.detailByName,
          [scenarioName]: detail,
        },
      }));
    } catch (error) {
      set({ loadingDetail: false, error: toErrorMessage(error) });
    }
  },
  clear: () => {
    set({
      list: [],
      detailByName: {},
      loadingList: false,
      loadingDetail: false,
      error: null,
      lastFetchedAt: null,
    });
  },
}));
