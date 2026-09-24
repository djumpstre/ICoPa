import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { endpoints } from "../../src/core/api/endpoints";
import { useAuthStore } from "../../src/stores/auth.store";
import { useExperimentStore } from "../../src/stores/experiment.store";

beforeEach(() => {
  useAuthStore.getState().setSession({ token: "fixture" });
  useExperimentStore.getState().clear();
});

afterEach(() => {
  useAuthStore.getState().clearSession();
  useExperimentStore.getState().clear();
  vi.unstubAllGlobals();
});

it("uses canonical run routes and encodes list filters", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response("[]"));
  vi.stubGlobal("fetch", fetch);
  await useExperimentStore.getState().fetchGeneratedRuns({ expName: "example & test", genVersion: 2 });
  const url = new URL(fetch.mock.calls[0][0] as string);
  expect(url.pathname).toBe("/profiling_exps/runs/");
  expect(url.searchParams.get("exp_name")).toBe("example & test");
  expect(url.searchParams.get("gen_version")).toBe("2");
  expect(endpoints.experiment.runDetail(42)).toBe("profiling_exps/runs/42/");
  expect(endpoints.experiment.runRerun(42)).toBe("profiling_exps/runs/42/rerun/");
});
