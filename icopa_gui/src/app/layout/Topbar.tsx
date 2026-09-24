import LogoutIcon from "@mui/icons-material/Logout";
import MenuIcon from "@mui/icons-material/Menu";
import { AppBar, Box, Button, IconButton, Toolbar, Tooltip, Typography } from "@mui/material";
import { useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuthStore } from "../../stores/auth.store";
import { useUiStore } from "../../stores/ui.store";
import { navigationWidth } from "./dimensions";

const titleMap: Record<string, string> = {
  "/cloud": "Cloud VMs",
  "/inventory": "Inventory",
  "/runtime": "Runtime Environments",
  "/scenario": "Scenarios",
  "/experiment/runs": "Experiment Runs",
  "/experiment/comparison": "Comparison",
  "/experiment/comparision_store": "Saved Comparisons",
  "/experiment": "Experiments",
};

export function Topbar({ onOpenNavigation }: { onOpenNavigation: () => void }) {
  const location = useLocation();
  const navigate = useNavigate();
  const logout = useAuthStore((state) => state.logout);
  const user = useAuthStore((state) => state.user);
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const drawerWidth = collapsed ? navigationWidth.collapsed : navigationWidth.expanded;

  const title = useMemo(() => {
    const matchedKey = Object.keys(titleMap)
      .sort((a, b) => b.length - a.length)
      .find((key) => location.pathname.startsWith(key));
    return matchedKey ? titleMap[matchedKey] : "ICoPa";
  }, [location.pathname]);

  return (
    <AppBar position="fixed" color="transparent" sx={{ backdropFilter: "blur(8px)", borderBottom: 1, borderColor: "divider", width: { xs: "100%", md: `calc(100% - ${drawerWidth}px)` } }}>
      <Toolbar sx={{ justifyContent: "space-between", gap: 1 }}>
        <Tooltip title="Open navigation">
          <IconButton aria-label="Open navigation" onClick={onOpenNavigation} sx={{ display: { md: "none" }, flexShrink: 0 }}>
            <MenuIcon />
          </IconButton>
        </Tooltip>
        <Box sx={{ minWidth: 0, flexGrow: 1 }}>
          <Typography variant="caption" color="secondary.main" sx={{ textTransform: "uppercase", letterSpacing: 0 }}>
            ICoPa
          </Typography>
          <Typography variant="h6" sx={{ fontSize: 18, lineHeight: 1.2, overflowWrap: "anywhere" }}>{title}</Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Typography variant="body2" color="text.secondary" sx={{ display: { xs: "none", sm: "block" } }}>
            {user?.username ?? "unknown-user"}
          </Typography>
          <Button
            variant="contained"
            size="small"
            endIcon={<LogoutIcon />}
            onClick={async () => {
              await logout();
              navigate("/login", { replace: true });
            }}
          >
            Logout
          </Button>
        </Box>
      </Toolbar>
    </AppBar>
  );
}
