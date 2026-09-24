import { create } from "zustand";

import { endpoints } from "../core/api/endpoints";
import { ApiError, toErrorMessage } from "../core/api/errors";
import { requestJson } from "../core/api/http";
import { RuntimeEnvironment } from "../features/runtime/types";
import { useAuthStore } from "./auth.store";

interface RuntimeStore {
  list: RuntimeEnvironment[];
  detailByName: Record<string, RuntimeEnvironment>;
  loadingList: boolean;
  loadingDetail: boolean;
  error: string | null;
  lastFetchedAt: string | null;
  fetchList: () => Promise<void>;
  fetchDetail: (envName: string) => Promise<void>;
  clear: () => void;
}

function getRequiredToken(): string {
  const token = useAuthStore.getState().token;
  if (!token) {
    throw new ApiError("Missing access token", 401);
  }
  return token;
}

export const useRuntimeStore = create<RuntimeStore>((set) => ({
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
      const list = await requestJson<RuntimeEnvironment[]>(endpoints.runtime.list, { token });
      set({ list, loadingList: false, lastFetchedAt: new Date().toISOString() });
    } catch (error) {
      set({ loadingList: false, error: toErrorMessage(error) });
    }
  },
  fetchDetail: async (envName) => {
    set({ loadingDetail: true, error: null });
    try {
      const token = getRequiredToken();
      const detail = await requestJson<RuntimeEnvironment>(endpoints.runtime.detail(envName), { token });
      set((state) => ({
        loadingDetail: false,
        detailByName: {
          ...state.detailByName,
          [envName]: detail,
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
