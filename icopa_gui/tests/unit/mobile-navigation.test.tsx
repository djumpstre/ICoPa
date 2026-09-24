import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it } from "vitest";

import { AppLayout } from "../../src/app/layout/AppLayout";
import { AppThemeProvider } from "../../src/app/AppThemeProvider";

afterEach(cleanup);

it("opens mobile navigation and closes it after selecting a destination", async () => {
  render(<MemoryRouter><AppThemeProvider><AppLayout /></AppThemeProvider></MemoryRouter>);
  expect(screen.queryByRole("link", { name: "Inventory" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
  fireEvent.click(await screen.findByRole("link", { name: "Inventory" }));
  await waitFor(() => expect(screen.queryByRole("link", { name: "Inventory" })).toBeNull());
});
