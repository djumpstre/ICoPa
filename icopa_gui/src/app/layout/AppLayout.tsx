import { Box, Toolbar } from "@mui/material";
import { useState } from "react";
import { Outlet } from "react-router-dom";

import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

export function AppLayout() {
  const [navigationOpen, setNavigationOpen] = useState(false);
  return (
    <Box sx={{ display: "flex", minHeight: "100vh" }}>
      <Topbar onOpenNavigation={() => setNavigationOpen(true)} />
      <Sidebar mobileOpen={navigationOpen} onCloseNavigation={() => setNavigationOpen(false)} />
      <Box component="main" sx={{ flexGrow: 1, p: { xs: 2, md: 3 }, width: "100%", minWidth: 0, overflowWrap: "anywhere" }}>
        <Toolbar />
        <Outlet />
      </Box>
    </Box>
  );
}
