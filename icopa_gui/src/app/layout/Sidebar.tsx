import DashboardIcon from "@mui/icons-material/Dashboard";
import CloudQueueIcon from "@mui/icons-material/CloudQueue";
import DeviceHubIcon from "@mui/icons-material/DeviceHub";
import DnsIcon from "@mui/icons-material/Dns";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import CloseIcon from "@mui/icons-material/Close";
import HubIcon from "@mui/icons-material/Hub";
import MemoryIcon from "@mui/icons-material/Memory";
import ScienceIcon from "@mui/icons-material/Science";
import TimelineIcon from "@mui/icons-material/Timeline";
import {
  Box,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import { NavLink, useLocation } from "react-router-dom";

import { useUiStore } from "../../stores/ui.store";
import { navigationWidth } from "./dimensions";

const navItems = [
  { to: "/cloud", label: "Cloud VMs", icon: <CloudQueueIcon /> },
  { to: "/inventory", label: "Inventory", icon: <DnsIcon /> },
  { to: "/runtime", label: "Runtime", icon: <MemoryIcon /> },
  { to: "/scenario", label: "Scenario", icon: <DeviceHubIcon /> },
  { to: "/experiment", label: "Experiments", icon: <ScienceIcon /> },
  { to: "/experiment/runs", label: "Experiment Runs", icon: <TimelineIcon /> },
  { to: "/experiment/comparison", label: "Comparison", icon: <HubIcon /> },
  { to: "/experiment/comparision_store", label: "Saved Comparisons", icon: <HubIcon /> },
];

function isNavSelected(pathname: string, to: string): boolean {
  if (to === "/experiment") {
    if (pathname === "/experiment") {
      return true;
    }
    return (
      pathname.startsWith("/experiment/") &&
      !pathname.startsWith("/experiment/runs") &&
      !pathname.startsWith("/experiment/comparison") &&
      !pathname.startsWith("/experiment/comparision_store")
    );
  }
  return pathname === to || pathname.startsWith(`${to}/`);
}

export function Sidebar({ mobileOpen, onCloseNavigation }: { mobileOpen: boolean; onCloseNavigation: () => void }) {
  const location = useLocation();
  const desktop = useMediaQuery(useTheme().breakpoints.up("md"));
  const collapsedPreference = useUiStore((state) => state.sidebarCollapsed);
  const sidebarCollapsed = desktop && collapsedPreference;
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);

  const drawerWidth = sidebarCollapsed ? navigationWidth.collapsed : navigationWidth.expanded;

  return (
    <Drawer
      variant={desktop ? "permanent" : "temporary"}
      open={desktop || mobileOpen}
      onClose={onCloseNavigation}
      sx={{
        width: desktop ? drawerWidth : 0,
        flexShrink: 0,
        [`& .MuiDrawer-paper`]: {
          width: drawerWidth,
          boxSizing: "border-box",
          transition: "width 0.2s ease",
          overflowX: "hidden",
        },
      }}
    >
      <Toolbar sx={{ display: "flex", justifyContent: sidebarCollapsed ? "center" : "space-between", px: 1.5 }}>
        {!sidebarCollapsed && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <DashboardIcon color="primary" />
            <Typography variant="subtitle1" fontWeight={700}>
              ICoPa GUI
            </Typography>
          </Box>
        )}
        <Tooltip title={!desktop ? "Close navigation" : sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}>
          <IconButton
            aria-label={!desktop ? "Close navigation" : sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}
            onClick={desktop ? toggleSidebar : onCloseNavigation} size="small"
          >
            {!desktop ? <CloseIcon /> : sidebarCollapsed ? <ChevronRightIcon /> : <ChevronLeftIcon />}
          </IconButton>
        </Tooltip>
      </Toolbar>
      <Divider />
      <List sx={{ pt: 1 }}>
        {navItems.map((item) => {
          const selected = isNavSelected(location.pathname, item.to);
          return (
            <ListItem key={item.to} disablePadding sx={{ px: 1 }}>
              <Tooltip title={sidebarCollapsed ? item.label : ""} placement="right">
                <ListItemButton component={NavLink} to={item.to} selected={selected} onClick={onCloseNavigation} sx={{ borderRadius: 2 }}>
                  <ListItemIcon sx={{ minWidth: sidebarCollapsed ? 0 : 40, justifyContent: "center" }}>
                    {item.icon}
                  </ListItemIcon>
                  {!sidebarCollapsed && <ListItemText primary={item.label} />}
                </ListItemButton>
              </Tooltip>
            </ListItem>
          );
        })}
      </List>
    </Drawer>
  );
}
