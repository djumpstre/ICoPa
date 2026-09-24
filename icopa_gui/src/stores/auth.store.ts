import { create } from "zustand";
import { persist } from "zustand/middleware";
import { z } from "zod";

import { endpoints } from "../core/api/endpoints";
import { requestJson } from "../core/api/http";

const loginResponseSchema = z.object({
  access: z.string(),
  refresh: z.string().optional(),
  user: z
    .object({
      id: z.number().optional(),
      username: z.string().optional(),
      email: z.string().optional(),
    })
    .passthrough(),
});

export interface AuthUser {
  id?: number;
  username?: string;
  email?: string;
}

interface AuthStore {
  token: string | null;
  refreshToken: string | null;
  user: AuthUser | null;
  isAuthenticated: boolean;
  isLoggingIn: boolean;
  error: string | null;
  setSession: (session: { token: string; refreshToken?: string; user?: AuthUser | null }) => void;
  clearSession: () => void;
  hydrateFromStorage: () => void;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set, get) => ({
      token: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,
      isLoggingIn: false,
      error: null,
      setSession: ({ token, refreshToken, user }) => {
        set({
          token,
          refreshToken: refreshToken ?? null,
          user: user ?? null,
          isAuthenticated: true,
          error: null,
        });
      },
      clearSession: () => {
        set({
          token: null,
          refreshToken: null,
          user: null,
          isAuthenticated: false,
          isLoggingIn: false,
          error: null,
        });
      },
      hydrateFromStorage: () => {
        const token = get().token;
        set({ isAuthenticated: Boolean(token) });
      },
      login: async (username, password) => {
        set({ isLoggingIn: true, error: null });
        try {
          const payload = await requestJson<unknown>(endpoints.auth.login, {
            method: "POST",
            body: { username, password },
          });
          const parsed = loginResponseSchema.parse(payload);
          get().setSession({
            token: parsed.access,
            refreshToken: parsed.refresh,
            user: parsed.user,
          });
        } catch (error) {
          const message = error instanceof Error ? error.message : "Login failed";
          set({ error: message });
          throw error;
        } finally {
          set({ isLoggingIn: false });
        }
      },
      logout: async () => {
        const token = get().token;
        const refreshToken = get().refreshToken;
        get().clearSession();
        try {
          if (token) {
            await requestJson<void>(endpoints.auth.logout, {
              method: "POST",
              body: refreshToken ? { refresh: refreshToken } : {},
              headers: { Authorization: `Bearer ${token}` },
            });
          }
        } catch {
          // A network failure must not keep local credentials or data alive.
        }
      },
    }),
    {
      name: "icopa_gui_auth",
      partialize: (state) => ({
        token: state.token,
        refreshToken: state.refreshToken,
        user: state.user,
      }),
    },
  ),
);
