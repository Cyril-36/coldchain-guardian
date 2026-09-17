import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AuthGate } from "./auth/AuthGate";
import { AuthProvider } from "./auth/AuthProvider";
import { DashboardPage } from "./dashboard/DashboardPage";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <AuthGate>
        <DashboardPage />
      </AuthGate>
    </AuthProvider>
  </StrictMode>,
);
