import { Navigate, Outlet, useLocation } from "react-router-dom";

import { LoginPage } from "../features/auth/pages/LoginPage";
import { useAuthStore } from "../stores/auth.store";

export function ProtectedRoute() {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}

export function LoginRoute() {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  if (isAuthenticated) {
    return <Navigate to="/inventory" replace />;
  }
  return <LoginPage />;
}
