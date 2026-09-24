import { create } from "zustand";
import { persist } from "zustand/middleware";

interface UiStore {
  sidebarCollapsed: boolean;
  searchByRoute: Record<string, string>;
  tableSortByRoute: Record<string, string>;
  toggleSidebar: () => void;
  setSearch: (routeKey: string, search: string) => void;
  setTableSort: (routeKey: string, sort: string) => void;
  clearAccountPreferences: () => void;
}

export const useUiStore = create<UiStore>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      searchByRoute: {},
      tableSortByRoute: {},
      clearAccountPreferences: () => set({ searchByRoute: {}, tableSortByRoute: {} }),
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setSearch: (routeKey, search) =>
        set((state) => ({
          searchByRoute: {
            ...state.searchByRoute,
            [routeKey]: search,
          },
        })),
      setTableSort: (routeKey, sort) =>
        set((state) => ({
          tableSortByRoute: {
            ...state.tableSortByRoute,
            [routeKey]: sort,
          },
        })),
    }),
    {
      name: "icopa_gui_ui",
      partialize: (state) => ({
        sidebarCollapsed: state.sidebarCollapsed,
        searchByRoute: state.searchByRoute,
        tableSortByRoute: state.tableSortByRoute,
      }),
    },
  ),
);
