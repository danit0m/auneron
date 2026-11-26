import { Link, useSearchParams } from "react-router-dom";
import { Header } from "../components/layout/Header";
import { useAuth } from "../hooks/useAuth";
import { ProtectedRoute } from "../routes/ProtectedRoute";
import type { Permission } from "../types/auth";
import ExecutiveCenter from "./ExecutiveCenter";
import Brain from "./Brain";
import Recomendacoes from "./Recomendacoes";
import "./IntelligenceCenter.css";

const views = [
  { id: "resumo", label: "Visão geral", permission: "executive.view", Component: ExecutiveCenter },
  { id: "recomendacoes", label: "Recomendações", permission: "approval:read", Component: Recomendacoes },
  { id: "analises", label: "Análises e alertas", permission: "brain.view", Component: Brain },
] as const satisfies readonly {
  id: string;
  label: string;
  permission: Permission;
  Component: typeof ExecutiveCenter;
}[];

export default function IntelligenceCenter() {
  const { hasPermission } = useAuth();
  const [params] = useSearchParams();
  const available = views.filter((view) => hasPermission(view.permission));
  const selected = views.find((view) => view.id === params.get("visao")) ?? available[0];
  if (!selected) return null;
  const Content = selected.Component;

  return (
    <div className="page intelligence-center">
      <Header title="Central de Inteligência" subtitle="Entenda o que merece atenção e acompanhe os próximos passos" />
      <div className="intelligence-navigation">
        <nav aria-label="Seções da Central de Inteligência">
          {available.map((view) => (
            <Link
              key={view.id}
              to={`/inteligencia?visao=${view.id}`}
              aria-current={selected.id === view.id ? "page" : undefined}
            >
              {view.label}
            </Link>
          ))}
        </nav>
        <p>Confira os resultados registrados, suas justificativas e as recomendações disponíveis.</p>
      </div>
      <div className="intelligence-content">
        <ProtectedRoute permission={selected.permission}>
          <Content key={selected.id} />
        </ProtectedRoute>
      </div>
    </div>
  );
}
