import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { initializeSessionLifecycle } from "../../src/app/session";
import { requestJson } from "../../src/core/api/http";
import { requestArtifactJson, requestArtifactText } from "../../src/core/api/artifacts";
import { useAuthStore } from "../../src/stores/auth.store";
import { useExperimentStore } from "../../src/stores/experiment.store";
import { useInventoryStore } from "../../src/stores/inventory.store";
import { useRunComparisonStore } from "../../src/stores/runComparison.store";
import { useRuntimeStore } from "../../src/stores/runtime.store";

describe("session isolation", () => {
  let dispose: () => void;

  beforeEach(() => {
    useAuthStore.getState().clearSession();
    dispose = initializeSessionLifecycle();
    useAuthStore.getState().setSession({ token: "account-a" });
  });

  afterEach(() => {
    useAuthStore.getState().clearSession();
    dispose();
    vi.unstubAllGlobals();
  });

  it("does not send artifact tokens to another origin", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(requestArtifactText("https://example.invalid/private.csv", { token: "account-a" }))
      .rejects.toThrow("configured Hub");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects artifact data read after an account switch", async () => {
    let complete!: (value: string) => void;
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200,
      text: () => new Promise<string>((resolve) => { complete = resolve; }),
    })));
    const pending = requestArtifactText("/profiling_exps/runs/1/metrics/", { token: "account-a" });
    await vi.waitFor(() => expect(complete).toBeTypeOf("function"));
    useAuthStore.getState().setSession({ token: "account-b" });
    complete("private account-a data");
    await expect(pending).rejects.toThrow("session changed");
  });

  it("expires the session on an unauthorized artifact request", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 401 })));
    await expect(requestArtifactJson("/profiling_exps/runs/1/metrics/", { token: "account-a" }))
      .rejects.toThrow("Authentication required");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("authenticates Hub artifact requests and disables redirects", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ rtt_ms: 2 })));
    vi.stubGlobal("fetch", fetch);
    await expect(requestArtifactJson("/profiling_exps/runs/1/metrics/", { token: "account-a" }))
      .resolves.toEqual({ rtt_ms: 2 });
    expect(fetch).toHaveBeenCalledWith("http://127.0.0.1:8000/profiling_exps/runs/1/metrics/", expect.objectContaining({
      redirect: "error", headers: { Authorization: "Bearer account-a" },
    }));
  });

  it("clears account data and persisted comparison selection on logout", () => {
    useExperimentStore.setState({ experiments: [{ name: "private experiment" }] });
    useRunComparisonStore.getState().addRun(5);
    useAuthStore.getState().clearSession();
    expect(useExperimentStore.getState().experiments).toEqual([]);
    expect(useRunComparisonStore.getState().selectedRunIds).toEqual([]);
    expect(JSON.parse(localStorage.getItem("icopa-run-comparison")!).state.selectedRunIds).toEqual([]);
    expect(useInventoryStore.getState().detailByName).toEqual({});
  });

  it("discards a late response from a previous account", async () => {
    let complete!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { complete = resolve; })));
    const pending = useExperimentStore.getState().fetchExperiments();
    useAuthStore.getState().setSession({ token: "account-b" });
    complete(new Response(JSON.stringify([{ name: "account-a secret" }])));
    await pending;
    expect(useExperimentStore.getState().experiments).toEqual([]);
  });

  it("checks the session again after reading a response body", async () => {
    let complete!: (value: unknown) => void;
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200,
      json: () => new Promise((resolve) => { complete = resolve; }),
    })));
    const pending = useRuntimeStore.getState().fetchList();
    await vi.waitFor(() => expect(complete).toBeTypeOf("function"));
    useAuthStore.getState().clearSession();
    useAuthStore.getState().setSession({ token: "account-a" });
    complete([{ name: "old session data" }]);
    await pending;
    expect(useRuntimeStore.getState().list).toEqual([]);
  });

  it("expires the current session on 401", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "Token expired" }),
      { status: 401, headers: { "content-type": "application/json" } },
    )));
    await expect(requestJson("inventory/", { token: "account-a" })).rejects.toThrow("Token expired");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("does not expire a new session when an old request returns 401", async () => {
    let complete!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { complete = resolve; })));
    const pending = requestJson("inventory/", { token: "account-a" });
    useAuthStore.getState().setSession({ token: "account-b" });
    complete(new Response("Expired", { status: 401 }));
    await expect(pending).rejects.toThrow("session changed");
    expect(useAuthStore.getState().token).toBe("account-b");
  });

  it("clears locally before logout finishes without clearing a subsequent login", async () => {
    let complete!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { complete = resolve; })));
    const pending = useAuthStore.getState().logout();
    expect(useAuthStore.getState().token).toBeNull();
    useAuthStore.getState().setSession({ token: "account-b" });
    complete(new Response(null, { status: 204 }));
    await pending;
    expect(useAuthStore.getState().token).toBe("account-b");
  });
});
