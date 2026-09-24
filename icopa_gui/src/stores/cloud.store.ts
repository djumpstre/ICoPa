import { create } from "zustand";

import { endpoints } from "../core/api/endpoints";
import { ApiError, toErrorMessage } from "../core/api/errors";
import { requestJson } from "../core/api/http";
import type {
  CloudCheckDispatchResponse,
  CloudProvisioningVm,
  CloudStartDispatchResponse,
  CloudStopDispatchResponse,
  CloudUploadResponse,
} from "../features/cloud/types";
import { useAuthStore } from "./auth.store";

interface CloudStore {
  list: CloudProvisioningVm[];
  detailById: Record<number, CloudProvisioningVm>;
  loadingList: boolean;
  loadingDetail: boolean;
  actionLoadingById: Record<number, boolean>;
  error: string | null;
  fetchList: (provider?: string) => Promise<void>;
  fetchDetail: (id: number) => Promise<void>;
  uploadTemplate: (file: File) => Promise<CloudUploadResponse | null>;
  startCreate: (id: number) => Promise<void>;
  startVm: (id: number) => Promise<void>;
  deleteVm: (id: number) => Promise<void>;
  stopVm: (id: number) => Promise<void>;
  triggerCheck: (id: number) => Promise<void>;
  clear: () => void;
}

function getRequiredToken(): string {
  const token = useAuthStore.getState().token;
  if (!token) {
    throw new ApiError("Missing access token", 401);
  }
  return token;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export const useCloudStore = create<CloudStore>((set, get) => ({
  list: [],
  detailById: {},
  loadingList: false,
  loadingDetail: false,
  actionLoadingById: {},
  error: null,
  fetchList: async (provider = "AZURE") => {
    set({ loadingList: true, error: null });
    try {
      const token = getRequiredToken();
      const list = await requestJson<CloudProvisioningVm[]>(endpoints.cloud.list(provider), { token });
      set({ list, loadingList: false });
    } catch (error) {
      set({ loadingList: false, error: toErrorMessage(error) });
    }
  },
  fetchDetail: async (id) => {
    set({ loadingDetail: true, error: null });
    try {
      const token = getRequiredToken();
      const detail = await requestJson<CloudProvisioningVm>(endpoints.cloud.detail(id), { token });
      set((state) => ({
        loadingDetail: false,
        detailById: {
          ...state.detailById,
          [id]: detail,
        },
      }));
    } catch (error) {
      set({ loadingDetail: false, error: toErrorMessage(error) });
    }
  },
  uploadTemplate: async (file) => {
    set({ error: null });
    try {
      const token = getRequiredToken();
      const form = new FormData();
      form.append("file", file);
      const response = await requestJson<CloudUploadResponse>(endpoints.cloud.upload, {
        method: "POST",
        token,
        body: form,
      });
      await get().fetchList("AZURE");
      return response;
    } catch (error) {
      set({ error: toErrorMessage(error) });
      return null;
    }
  },
  startCreate: async (id) => {
    set((state) => ({ actionLoadingById: { ...state.actionLoadingById, [id]: true }, error: null }));
    try {
      const token = getRequiredToken();
      await requestJson<CloudProvisioningVm>(endpoints.cloud.create(id), {
        method: "POST",
        token,
        body: {},
      });
      await Promise.all([get().fetchList("AZURE"), get().fetchDetail(id)]);
    } catch (error) {
      set({ error: toErrorMessage(error) });
    } finally {
      set((state) => ({ actionLoadingById: { ...state.actionLoadingById, [id]: false } }));
    }
  },
  deleteVm: async (id) => {
    set((state) => ({ actionLoadingById: { ...state.actionLoadingById, [id]: true }, error: null }));
    try {
      const token = getRequiredToken();
      await requestJson<CloudProvisioningVm>(endpoints.cloud.delete(id), {
        method: "POST",
        token,
        body: {},
      });
      await Promise.all([get().fetchList("AZURE"), get().fetchDetail(id)]);
    } catch (error) {
      set({ error: toErrorMessage(error) });
    } finally {
      set((state) => ({ actionLoadingById: { ...state.actionLoadingById, [id]: false } }));
    }
  },
  startVm: async (id) => {
    set((state) => ({ actionLoadingById: { ...state.actionLoadingById, [id]: true }, error: null }));
    try {
      const token = getRequiredToken();
      const dispatch = await requestJson<CloudStartDispatchResponse>(endpoints.cloud.start(id), {
        method: "POST",
        token,
        body: {},
      });
      const startRequestId = Number(dispatch.start_request_id);
      const deadline = Date.now() + 90_000;
      if (Number.isFinite(startRequestId) && startRequestId > 0) {
        while (Date.now() < deadline) {
          const startDetail = await requestJson<{ status: string }>(
            endpoints.cloud.startDetail(id, startRequestId),
            { token },
          );
          await get().fetchDetail(id);
          const status = String(startDetail.status || "").toUpperCase();
          if (status === "PASS" || status === "FAIL") {
            break;
          }
          await sleep(2500);
        }
      }
      await Promise.all([get().fetchList("AZURE"), get().fetchDetail(id)]);
    } catch (error) {
      set({ error: toErrorMessage(error) });
    } finally {
      set((state) => ({ actionLoadingById: { ...state.actionLoadingById, [id]: false } }));
    }
  },
  stopVm: async (id) => {
    set((state) => ({ actionLoadingById: { ...state.actionLoadingById, [id]: true }, error: null }));
    try {
      const token = getRequiredToken();
      const dispatch = await requestJson<CloudStopDispatchResponse>(endpoints.cloud.stop(id), {
        method: "POST",
        token,
        body: {},
      });
      const stopRequestId = Number(dispatch.stop_request_id);
      const deadline = Date.now() + 90_000;
      if (Number.isFinite(stopRequestId) && stopRequestId > 0) {
        while (Date.now() < deadline) {
          const stopDetail = await requestJson<{ status: string }>(
            endpoints.cloud.stopDetail(id, stopRequestId),
            { token },
          );
          await get().fetchDetail(id);
          const status = String(stopDetail.status || "").toUpperCase();
          if (status === "PASS" || status === "FAIL") {
            break;
          }
          await sleep(2500);
        }
      }
      await Promise.all([get().fetchList("AZURE"), get().fetchDetail(id)]);
    } catch (error) {
      set({ error: toErrorMessage(error) });
    } finally {
      set((state) => ({ actionLoadingById: { ...state.actionLoadingById, [id]: false } }));
    }
  },
  triggerCheck: async (id) => {
    set((state) => ({ actionLoadingById: { ...state.actionLoadingById, [id]: true }, error: null }));
    try {
      const token = getRequiredToken();
      const dispatch = await requestJson<CloudCheckDispatchResponse>(endpoints.cloud.check(id), {
        method: "POST",
        token,
        body: {},
      });
      const checkRequestId = Number(dispatch.check_request_id);
      const deadline = Date.now() + 60_000;
      if (Number.isFinite(checkRequestId) && checkRequestId > 0) {
        while (Date.now() < deadline) {
          const checkDetail = await requestJson<{ status: string }>(
            endpoints.cloud.checkDetail(id, checkRequestId),
            { token },
          );
          await get().fetchDetail(id);
          const status = String(checkDetail.status || "").toUpperCase();
          if (status === "PASS" || status === "FAIL") {
            break;
          }
          await sleep(2500);
        }
      }
      await Promise.all([get().fetchList("AZURE"), get().fetchDetail(id)]);
    } catch (error) {
      set({ error: toErrorMessage(error) });
    } finally {
      set((state) => ({ actionLoadingById: { ...state.actionLoadingById, [id]: false } }));
    }
  },
  clear: () => {
    set({
      list: [],
      detailById: {},
      loadingList: false,
      loadingDetail: false,
      actionLoadingById: {},
      error: null,
    });
  },
}));
