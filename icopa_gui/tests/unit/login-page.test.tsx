import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it } from "vitest";

import { LoginPage } from "../../src/features/auth/pages/LoginPage";

afterEach(cleanup);

it("never prepopulates development credentials", () => {
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  expect(screen.getByLabelText(/Username/)).toHaveValue("");
  expect(screen.getByLabelText(/Password/)).toHaveValue("");
});
