import { Navigate, createBrowserRouter } from "react-router-dom";

import { AppLayout } from "./layout/AppLayout";
import { LoginRoute, ProtectedRoute } from "./RouteGuards";

export const router = createBrowserRouter([
  {
    path: "/login",
    element: <LoginRoute />,
  },
  {
    path: "/",
    element: <ProtectedRoute />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="/inventory" replace /> },
          { path: "cloud", lazy: async () => ({ Component: (await import("../features/cloud/pages/CloudVmListPage")).CloudVmListPage }) },
          { path: "cloud/:vmId", lazy: async () => ({ Component: (await import("../features/cloud/pages/CloudVmDetailPage")).CloudVmDetailPage }) },
          { path: "inventory", lazy: async () => ({ Component: (await import("../features/inventory/pages/InventoryListPage")).InventoryListPage }) },
          { path: "inventory/:vmName", lazy: async () => ({ Component: (await import("../features/inventory/pages/InventoryDetailPage")).InventoryDetailPage }) },
          { path: "runtime", lazy: async () => ({ Component: (await import("../features/runtime/pages/RuntimeListPage")).RuntimeListPage }) },
          { path: "runtime/:envName", lazy: async () => ({ Component: (await import("../features/runtime/pages/RuntimeDetailPage")).RuntimeDetailPage }) },
          { path: "scenario", lazy: async () => ({ Component: (await import("../features/scenario/pages/ScenarioListPage")).ScenarioListPage }) },
          { path: "scenario/:scenarioName", lazy: async () => ({ Component: (await import("../features/scenario/pages/ScenarioDetailPage")).ScenarioDetailPage }) },
          { path: "experiment", lazy: async () => ({ Component: (await import("../features/experiment/pages/ExperimentListPage")).ExperimentListPage }) },
          { path: "experiment/:expName", lazy: async () => ({ Component: (await import("../features/experiment/pages/ExperimentDetailPage")).ExperimentDetailPage }) },
          { path: "experiment/runs", lazy: async () => ({ Component: (await import("../features/experiment/pages/ExperimentRunListPage")).ExperimentRunListPage }) },
          { path: "experiment/comparison", lazy: async () => ({ Component: (await import("../features/experiment/pages/ExperimentRunComparisionPage")).ExperimentRunComparisionPage }) },
          { path: "experiment/comparision_store", lazy: async () => ({ Component: (await import("../features/experiment/pages/StoredComparisionListPage")).StoredComparisionListPage }) },
          { path: "experiment/comparision_store/:comparisionId", lazy: async () => ({ Component: (await import("../features/experiment/pages/StoredComparisionDetailPage")).StoredComparisionDetailPage }) },
          { path: "experiment/runs/:runId", lazy: async () => ({ Component: (await import("../features/experiment/pages/ExperimentRunDetailPage")).ExperimentRunDetailPage }) },
        ],
      },
    ],
  },
]);
