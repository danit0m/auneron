import {
  BrowserRouter,
  Route,
  Routes,
} from "react-router-dom";

import { Layout } from "./components/layout/Layout";
import {
  ProtectedRoute,
} from "./routes/ProtectedRoute";
import AgentOperations from "./pages/AgentOperations";
import Brain from "./pages/Brain";
import Clientes from "./pages/Clientes";
import { Dashboard } from "./pages/Dashboard";
import ExecutiveCenter from "./pages/ExecutiveCenter";
import { Upload } from "./pages/Upload";
import AccessDenied from "./pages/AccessDenied";
import UIShowcase from "./pages/admin/UIShowcase";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route
            path="/"
            element={
              <ProtectedRoute permission="dashboard.view">
                <Dashboard />
              </ProtectedRoute>
            }
          />

          <Route
            path="/clientes"
            element={
              <ProtectedRoute permission="clients.view">
                <Clientes />
              </ProtectedRoute>
            }
          />

          <Route
            path="/upload"
            element={
              <ProtectedRoute permission="imports.execute">
                <Upload />
              </ProtectedRoute>
            }
          />

          <Route
            path="/executive-center"
            element={
              <ProtectedRoute permission="executive.view">
                <ExecutiveCenter />
              </ProtectedRoute>
            }
          />

          <Route
            path="/brain"
            element={
              <ProtectedRoute permission="brain.view">
                <Brain />
              </ProtectedRoute>
            }
          />

          <Route
            path="/agent-operations"
            element={
              <ProtectedRoute permission="administration.ai-operations">
                <AgentOperations />
              </ProtectedRoute>
            }
          />

          <Route
            path="/admin/ui-showcase"
            element={
              <ProtectedRoute permission="developer.ui-showcase">
                <UIShowcase />
              </ProtectedRoute>
            }
          />

          <Route
            path="/access-denied"
            element={<AccessDenied />}
          />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
