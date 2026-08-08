import {
  BrowserRouter,
  Route,
  Routes,
} from "react-router-dom";

import { Layout } from "./components/layout/Layout";
import {
  ElevatedRoute,
} from "./routes/ElevatedRoute";
import {
  ProtectedRoute,
} from "./routes/ProtectedRoute";
import AgentOperations from "./pages/AgentOperations";
import Brain from "./pages/Brain";
import Clientes from "./pages/Clientes";
import { Dashboard } from "./pages/Dashboard";
import ExecutiveCenter from "./pages/ExecutiveCenter";
import Login from "./pages/Login";
import { Upload } from "./pages/Upload";
import AccessDenied from "./pages/AccessDenied";
import UIShowcase from "./pages/admin/UIShowcase";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/login"
          element={<Login />}
        />

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
              <ElevatedRoute
                permission="administration.ai-operations"
                resourceLabel="AI Operations"
              >
                <AgentOperations />
              </ElevatedRoute>
            }
          />

          <Route
            path="/admin/ui-showcase"
            element={
              <ElevatedRoute
                permission="developer.ui-showcase"
                resourceLabel="UI Showcase"
              >
                <UIShowcase />
              </ElevatedRoute>
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
