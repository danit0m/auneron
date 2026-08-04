import { BrainCircuit, AlertTriangle, ShieldCheck } from "lucide-react";

type Agent = {
  agent: string;
  knowledge: number;
};

type ExecutiveReport = {
  total: number;
  pending: number;
  resolved: number;
  critical: number;
  high: number;
  medium: number;
  info: number;
  priority: string;
  summary: string;
  agents: Agent[];
};

type Props = {
  report: ExecutiveReport | null;
};

export default function ExecutiveCard({ report }: Props) {
  if (!report) return null;

  const color =
    report.priority === "CRÍTICA"
      ? "#dc2626"
      : report.priority === "ALTA"
      ? "#f97316"
      : report.priority === "MÉDIA"
      ? "#facc15"
      : "#22c55e";

  return (
    <div className="executive-card">
      <div className="executive-header">
        <div className="executive-icon">
          <BrainCircuit size={28} />
        </div>

        <div>
          <small>EXECUTIVE AI</small>

          <h2>Resumo Executivo</h2>

          <p>{report.summary}</p>
        </div>
      </div>

      <div className="executive-grid">
        <div>
          <span>Prioridade</span>

          <strong style={{ color }}>
            {report.priority}
          </strong>
        </div>

        <div>
          <span>Conhecimentos</span>

          <strong>{report.total}</strong>
        </div>

        <div>
          <span>Pendentes</span>

          <strong>{report.pending}</strong>
        </div>

        <div>
          <span>Resolvidos</span>

          <strong>{report.resolved}</strong>
        </div>
      </div>

      <div className="executive-agents">
        <h4>Agentes ativos</h4>

        {report.agents.map((agent) => (
          <div
            key={agent.agent}
            className="executive-agent"
          >
            <ShieldCheck size={18} />

            <span>{agent.agent}</span>

            <strong>{agent.knowledge}</strong>
          </div>
        ))}
      </div>

      {report.critical > 0 && (
        <div className="executive-alert">
          <AlertTriangle size={18} />

          Existem alertas críticos que exigem atenção imediata.
        </div>
      )}
    </div>
  );
}