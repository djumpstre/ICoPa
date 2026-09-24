import { create } from "zustand";
import { persist } from "zustand/middleware";

interface RunComparisonStore {
  selectedRunIds: number[];
  isSelected: (runId: number) => boolean;
  addRun: (runId: number) => void;
  removeRun: (runId: number) => void;
  toggleRun: (runId: number) => void;
  clear: () => void;
}

export const useRunComparisonStore = create<RunComparisonStore>()(
  persist(
    (set, get) => ({
      selectedRunIds: [],
      isSelected: (runId) => get().selectedRunIds.includes(runId),
      addRun: (runId) =>
        set((state) => {
          if (state.selectedRunIds.includes(runId)) {
            return state;
          }
          return { selectedRunIds: [...state.selectedRunIds, runId] };
        }),
      removeRun: (runId) =>
        set((state) => ({
          selectedRunIds: state.selectedRunIds.filter((id) => id !== runId),
        })),
      toggleRun: (runId) =>
        set((state) => {
          if (state.selectedRunIds.includes(runId)) {
            return { selectedRunIds: state.selectedRunIds.filter((id) => id !== runId) };
          }
          return { selectedRunIds: [...state.selectedRunIds, runId] };
        }),
      clear: () => set({ selectedRunIds: [] }),
    }),
    {
      name: "icopa-run-comparison",
      partialize: (state) => ({ selectedRunIds: state.selectedRunIds }),
    },
  ),
);
