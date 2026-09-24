import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { AppThemeProvider } from "./app/AppThemeProvider";
import { useAuthStore } from "./stores/auth.store";
import { initializeSessionLifecycle } from "./app/session";

const disposeSessionLifecycle = initializeSessionLifecycle();
if (import.meta.hot) import.meta.hot.dispose(disposeSessionLifecycle);
useAuthStore.getState().hydrateFromStorage();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppThemeProvider>
      <App />
    </AppThemeProvider>
  </StrictMode>,
);
