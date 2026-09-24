import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "../../src/stores/auth.store";

describe("auth store", () => {
  beforeEach(() => {
    useAuthStore.getState().clearSession();
  });

  it("sets and clears a session", () => {
    useAuthStore.getState().setSession({
      token: "token-abc",
      refreshToken: "refresh-xyz",
      user: { username: "agent" },
    });

    const withSession = useAuthStore.getState();
    expect(withSession.isAuthenticated).toBe(true);
    expect(withSession.token).toBe("token-abc");
    expect(withSession.user?.username).toBe("agent");

    useAuthStore.getState().clearSession();

    const afterClear = useAuthStore.getState();
    expect(afterClear.isAuthenticated).toBe(false);
    expect(afterClear.token).toBeNull();
    expect(afterClear.user).toBeNull();
  });
});
