import { cleanup, render, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import { InventoryDetailPage } from "../../src/features/inventory/pages/InventoryDetailPage";
import { useInventoryStore } from "../../src/stores/inventory.store";

const originalFetchDetail = useInventoryStore.getState().fetchDetail;

afterEach(() => {
  cleanup();
  useInventoryStore.setState({ fetchDetail: originalFetchDetail });
  useInventoryStore.getState().clear();
});

it.each(["100%", "literal%25", "name with spaces"])("decodes a route name exactly once: %s", async (name) => {
  const fetchDetail = vi.fn(async () => undefined);
  useInventoryStore.setState({ fetchDetail });
  render(
    <MemoryRouter initialEntries={[`/inventory/${encodeURIComponent(name)}`]}>
      <Routes><Route path="/inventory/:vmName" element={<InventoryDetailPage />} /></Routes>
    </MemoryRouter>,
  );
  await waitFor(() => expect(fetchDetail).toHaveBeenCalledWith(name));
});
