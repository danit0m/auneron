import {
  BrowserRouter,
  Navigate,
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
import Approvals from "./pages/Approvals";
import Clientes from "./pages/Clientes";
import { Cockpit } from "./pages/Cockpit";
import IntelligenceCenter from "./pages/IntelligenceCenter";
import Login from "./pages/Login";
import TrocarSenha from "./pages/TrocarSenha";
import Recebiveis from "./pages/Recebiveis";
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

        <Route
          path="/trocar-senha"
          element={<TrocarSenha />}
        />

        <Route element={<Layout />}>
          <Route
            path="/inteligencia"
            element={
              <ProtectedRoute anyPermissions={["executive.view", "brain.view", "approval:read"]}>
                <IntelligenceCenter />
              </ProtectedRoute>
            }
          />
          <Route
            path="/"
            element={
              <ProtectedRoute permission="dashboard.view">
                <Cockpit />
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
                <Navigate to="/inteligencia?visao=resumo" replace />
              </ProtectedRoute>
            }
          />

          <Route
            path="/brain"
            element={
              <ProtectedRoute permission="brain.view">
                <Navigate to="/inteligencia?visao=analises" replace />
              </ProtectedRoute>
            }
          />

          <Route
            path="/approvals"
            element={
              <ProtectedRoute permission="approval:read">
                <Approvals />
              </ProtectedRoute>
            }
          />

          <Route
            path="/recebiveis"
            element={
              <ProtectedRoute permission="approval:read">
                <Recebiveis />
              </ProtectedRoute>
            }
          />

          <Route
            path="/recomendacoes"
            element={
              <ProtectedRoute permission="approval:read">
                <Navigate to="/inteligencia?visao=recomendacoes" replace />
              </ProtectedRoute>
            }
          />

          <Route
            path="/agent-operations"
            element={
              <ElevatedRoute
                permission="administration.ai-operations"
                resourceLabel="Saúde do sistema"
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
