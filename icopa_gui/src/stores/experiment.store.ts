import { create } from "zustand";

import { endpoints } from "../core/api/endpoints";
import { ApiError, toErrorMessage } from "../core/api/errors";
import { requestJson } from "../core/api/http";
import {
  ProfilingComparision,
  ProfilingExperiment,
  ProfilingGeneratedRun,
} from "../features/experiment/types";
import { useAuthStore } from "./auth.store";

interface ExperimentStore {
  experiments: ProfilingExperiment[];
  experimentDetailByName: Record<string, ProfilingExperiment>;
  generatedRuns: ProfilingGeneratedRun[];
  generatedRunDetailById: Record<number, ProfilingGeneratedRun>;
  comparisions: ProfilingComparision[];
  loading: boolean;
  error: string | null;
  lastFetchedAt: string | null;
  fetchExperiments: () => Promise<void>;
  fetchExperimentDetail: (expName: string) => Promise<void>;
  fetchGeneratedRuns: (options?: { expName?: string; genVersion?: number }) => Promise<void>;
  fetchGeneratedRunDetail: (runId: number) => Promise<void>;
  fetchComparisions: () => Promise<void>;
  createComparision: (payload: {
    name: string;
    description?: string;
    runIds: number[];
  }) => Promise<ProfilingComparision>;
  generateExperiment: (expName: string, options?: { replaceGeneration?: number }) => Promise<Record<string, unknown>>;
  startExperiment: (expName: string, options: { genVersion: number; runId?: number }) => Promise<Record<string, unknown>>;
  rerunExperimentRun: (runId: number, options?: { replaceCurrentRun?: boolean }) => Promise<Record<string, unknown>>;
  deleteGeneratedRun: (runId: number) => Promise<Record<string, unknown>>;
  updateGeneratedRunNote: (runId: number, note: string) => Promise<ProfilingGeneratedRun>;
  deleteRunsByGeneration: (expName: string, options: { genVersion: number }) => Promise<Record<string, unknown>>;
  clear: () => void;
}

function getRequiredToken(): string {
  const token = useAuthStore.getState().token;
  if (!token) {
    throw new ApiError("Missing access token", 401);
  }
  return token;
}

export const useExperimentStore = create<ExperimentStore>((set) => ({
  experiments: [],
  experimentDetailByName: {},
  generatedRuns: [],
  generatedRunDetailById: {},
  comparisions: [],
  loading: false,
  error: null,
  lastFetchedAt: null,
  fetchExperiments: async () => {
    set({ loading: true, error: null });
    try {
      const token = getRequiredToken();
      const experiments = await requestJson<ProfilingExperiment[]>(endpoints.experiment.list, { token });
      set({ experiments, loading: false, lastFetchedAt: new Date().toISOString() });
    } catch (error) {
      set({ loading: false, error: toErrorMessage(error) });
    }
  },
  fetchExperimentDetail: async (expName) => {
    set({ loading: true, error: null });
    try {
      const token = getRequiredToken();
      const detail = await requestJson<ProfilingExperiment>(endpoints.experiment.detail(expName), { token });
      set((state) => ({
        loading: false,
        experimentDetailByName: {
          ...state.experimentDetailByName,
          [expName]: detail,
        },
      }));
    } catch (error) {
      set({ loading: false, error: toErrorMessage(error) });
    }
  },
  fetchGeneratedRuns: async (options) => {
    set({ loading: true, error: null });
    try {
      const token = getRequiredToken();
      const searchParams = new URLSearchParams();
      const expName = options?.expName?.trim();
      if (expName) {
        searchParams.set("exp_name", expName);
      }
      if (typeof options?.genVersion === "number" && Number.isFinite(options.genVersion) && options.genVersion > 0) {
        searchParams.set("gen_version", String(options.genVersion));
      }
      const path = searchParams.size > 0 ? `${endpoints.experiment.runList}?${searchParams.toString()}` : endpoints.experiment.runList;
      const generatedRuns = await requestJson<ProfilingGeneratedRun[]>(path, {
        token,
      });
      set({ generatedRuns, loading: false, lastFetchedAt: new Date().toISOString() });
    } catch (error) {
      set({ loading: false, error: toErrorMessage(error) });
    }
  },
  fetchGeneratedRunDetail: async (runId) => {
    set({ loading: true, error: null });
    try {
      const token = getRequiredToken();
      const detail = await requestJson<ProfilingGeneratedRun>(endpoints.experiment.runDetail(runId), {
        token,
      });
      set((state) => ({
        loading: false,
        generatedRunDetailById: {
          ...state.generatedRunDetailById,
          [runId]: detail,
        },
      }));
    } catch (error) {
      set({ loading: false, error: toErrorMessage(error) });
    }
  },
  fetchComparisions: async () => {
    set({ loading: true, error: null });
    try {
      const token = getRequiredToken();
      const comparisions = await requestJson<ProfilingComparision[]>(endpoints.experiment.comparisionList, {
        token,
      });
      set({ comparisions, loading: false, lastFetchedAt: new Date().toISOString() });
    } catch (error) {
      set({ loading: false, error: toErrorMessage(error) });
    }
  },
  createComparision: async (payload) => {
    try {
      const token = getRequiredToken();
      const response = await requestJson<ProfilingComparision>(endpoints.experiment.comparisionList, {
        method: "POST",
        token,
        body: {
          name: payload.name,
          description: payload.description ?? "",
          run_ids: payload.runIds,
        },
      });
      set((state) => ({
        error: null,
        comparisions: [response, ...state.comparisions.filter((item) => item.id !== response.id)],
      }));
      return response;
    } catch (error) {
      const message = toErrorMessage(error);
      set({ error: message });
      throw error;
    }
  },
  generateExperiment: async (expName, options) => {
    try {
      const token = getRequiredToken();
      const payload: Record<string, unknown> = {};
      if (
        typeof options?.replaceGeneration === "number" &&
        Number.isFinite(options.replaceGeneration) &&
        options.replaceGeneration > 0
      ) {
        payload.replace_gen_version = options.replaceGeneration;
      }
      const response = await requestJson<Record<string, unknown>>(endpoints.experiment.generate(expName), {
        method: "POST",
        token,
        body: payload,
      });
      set({ error: null });
      return response;
    } catch (error) {
      const message = toErrorMessage(error);
      set({ error: message });
      throw error;
    }
  },
  startExperiment: async (expName, options) => {
    try {
      const token = getRequiredToken();
      const payload: Record<string, unknown> = { gen_version: options.genVersion };
      if (typeof options.runId === "number" && Number.isFinite(options.runId)) {
        payload.run_id = options.runId;
      }
      const response = await requestJson<Record<string, unknown>>(endpoints.experiment.start(expName), {
        method: "POST",
        token,
        body: payload,
      });
      set({ error: null });
      return response;
    } catch (error) {
      const message = toErrorMessage(error);
      set({ error: message });
      throw error;
    }
  },
  rerunExperimentRun: async (runId, options) => {
    try {
      const token = getRequiredToken();
      const body: Record<string, unknown> = {};
      if (options?.replaceCurrentRun) {
        body.replace_current_run = true;
      }
      const response = await requestJson<Record<string, unknown>>(endpoints.experiment.runRerun(runId), {
        method: "POST",
        token,
        body,
      });
      set({ error: null });
      return response;
    } catch (error) {
      const message = toErrorMessage(error);
      set({ error: message });
      throw error;
    }
  },
  deleteGeneratedRun: async (runId) => {
    try {
      const token = getRequiredToken();
      const response = await requestJson<Record<string, unknown>>(endpoints.experiment.runDetail(runId), {
        method: "DELETE",
        token,
      });
      set((state) => {
        const nextDetailById = { ...state.generatedRunDetailById };
        delete nextDetailById[runId];
        return {
          error: null,
          generatedRunDetailById: nextDetailById,
          generatedRuns: state.generatedRuns.filter((item) => item.id !== runId),
        };
      });
      return response;
    } catch (error) {
      const message = toErrorMessage(error);
      set({ error: message });
      throw error;
    }
  },
  updateGeneratedRunNote: async (runId, note) => {
    try {
      const token = getRequiredToken();
      const response = await requestJson<{ run?: ProfilingGeneratedRun }>(endpoints.experiment.runDetail(runId), {
        method: "PATCH",
        token,
        body: { note },
      });
      const run = response.run;
      if (!run || typeof run.id !== "number") {
        throw new ApiError("Run update response did not include run detail.", 500);
      }
      set((state) => ({
        error: null,
        generatedRunDetailById: {
          ...state.generatedRunDetailById,
          [run.id]: run,
        },
        generatedRuns: state.generatedRuns.map((row) => (row.id === run.id ? { ...row, note: run.note } : row)),
      }));
      return run;
    } catch (error) {
      const message = toErrorMessage(error);
      set({ error: message });
      throw error;
    }
  },
  deleteRunsByGeneration: async (expName, options) => {
    try {
      const token = getRequiredToken();
      const query = new URLSearchParams();
      query.set("gen_version", String(options.genVersion));
      const response = await requestJson<Record<string, unknown>>(
        `${endpoints.experiment.runsByExperiment(expName)}?${query.toString()}`,
        {
          method: "DELETE",
          token,
        },
      );
      set({ error: null });
      return response;
    } catch (error) {
      const message = toErrorMessage(error);
      set({ error: message });
      throw error;
    }
  },
  clear: () => {
    set({
      experiments: [],
      experimentDetailByName: {},
      generatedRuns: [],
      generatedRunDetailById: {},
      comparisions: [],
      loading: false,
      error: null,
      lastFetchedAt: null,
    });
  },
}));
