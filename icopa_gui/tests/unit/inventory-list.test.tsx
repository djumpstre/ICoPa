import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import { InventoryListPage } from "../../src/features/inventory/pages/InventoryListPage";
import { useInventoryStore } from "../../src/stores/inventory.store";

const original = useInventoryStore.getState();

afterEach(() => {
  cleanup();
  useInventoryStore.setState({ fetchList: original.fetchList, checkVm: original.checkVm });
  useInventoryStore.getState().clear();
});

it("keeps VM actions outside the accordion toggle", () => {
  const checkVm = vi.fn(async () => undefined);
  useInventoryStore.setState({
    list: [{ name: "example-vm", group_name: "example" }],
    fetchList: vi.fn(async () => undefined), checkVm,
  });
  const { container } = render(<MemoryRouter><InventoryListPage /></MemoryRouter>);
  expect(container.querySelector("button button, button a")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Check VM" }));
  expect(checkVm).toHaveBeenCalledWith("example-vm");
  expect(screen.getByRole("link", { name: "Open Detail" })).toHaveAttribute("href", "/inventory/example-vm");
});
