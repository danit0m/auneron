import {
  BrowserRouter,
  Route,
  Routes,
} from "react-router-dom";

import { Layout } from "./components/layout/Layout";
import AgentOperations from "./pages/AgentOperations";
import Brain from "./pages/Brain";
import Clientes from "./pages/Clientes";
import { Dashboard } from "./pages/Dashboard";
import ExecutiveCenter from "./pages/ExecutiveCenter";
import { Upload } from "./pages/Upload";
import UIShowcase from "./pages/admin/UIShowcase";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route
            path="/"
            element={<Dashboard />}
          />

          <Route
            path="/clientes"
            element={<Clientes />}
          />

          <Route
            path="/upload"
            element={<Upload />}
          />

          <Route
            path="/executive-center"
            element={<ExecutiveCenter />}
          />

          <Route
            path="/brain"
            element={<Brain />}
          />

          <Route
            path="/agent-operations"
            element={<AgentOperations />}
          />

          {/*
            Sprint 8.1:
            rota administrativa preparada para receber
            proteção por RBAC + Credencial Elevada.
          */}
          <Route
            path="/admin/ui-showcase"
            element={<UIShowcase />}
          />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;