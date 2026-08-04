import {
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import "./styles/BrainExecutiveSummary.css";

interface ExecutiveAgentSummary {
  agent: string;
  knowledge: number;
}

interface ExecutiveReport {
  total: number;
  pending: number;
  resolved: number;
  critical: number;
  high: number;
  medium: number;
  info: number;
  priority: string;
  summary: string;
  agents: ExecutiveAgentSummary[];
}

interface BrainExecutiveSummaryProps {
  report: ExecutiveReport | null;
}

type PriorityProfile = {
  label: string;
  className: string;
  description: string;
};

function formatarPrioridade(
  prioridade: string,
): PriorityProfile {
  const valor = prioridade
    .trim()
    .toUpperCase();

  if (valor === "CRÍTICA") {
    return {
      label: "Crítica",
      className:
        "brain-executive-priority brain-executive-priority-critical",
      description:
        "Existem situações que exigem atenção imediata.",
    };
  }

  if (valor === "ALTA") {
    return {
      label: "Alta",
      className:
        "brain-executive-priority brain-executive-priority-high",
      description:
        "Existem situações relevantes que precisam de acompanhamento próximo.",
    };
  }

  if (valor === "MÉDIA") {
    return {
      label: "Média",
      className:
        "brain-executive-priority brain-executive-priority-medium",
      description:
        "O cenário exige acompanhamento preventivo.",
    };
  }

  return {
    label: "Normal",
    className:
      "brain-executive-priority brain-executive-priority-normal",
    description:
      "Não há alertas críticos no cenário atual.",
  };
}

function formatarNomeAgente(
  nome: string,
): string {
  return nome
    .replace(/Agent$/i, " Agent")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim();
}

function percentual(
  valor: number,
  total: number,
): number {
  if (total <= 0) {
    return 0;
  }

  return Math.min(
    Math.max(
      (valor / total) * 100,
      0,
    ),
    100,
  );
}

export default function BrainExecutiveSummary({
  report,
}: BrainExecutiveSummaryProps) {
  if (!report) {
    return (
      <article className="brain-executive-summary brain-executive-summary-empty">
        <BrainCircuit size={36} />

        <strong>
          Resumo executivo indisponível
        </strong>

        <p>
          Assim que o Brain produzir novos
          conhecimentos, a visão executiva
          será apresentada aqui.
        </p>
      </article>
    );
  }

  const prioridade =
    formatarPrioridade(
      report.priority,
    );

  const totalAgentes =
    report.agents.length;

  const conhecimentosPendentes =
    percentual(
      report.pending,
      report.total,
    );

  const conhecimentosResolvidos =
    percentual(
      report.resolved,
      report.total,
    );

  return (
    <article className="brain-executive-summary">
      <div className="brain-executive-summary-header">
        <div className="brain-executive-summary-title">
          <div className="brain-executive-summary-icon">
            <BrainCircuit size={24} />
          </div>

          <div>
            <span>
              Inteligência executiva
            </span>

            <h2>
              Resumo do Brain Center
            </h2>

            <p>
              {report.summary}
            </p>
          </div>
        </div>

        <div className={prioridade.className}>
          <strong>
            {prioridade.label}
          </strong>

          <span>
            Prioridade atual
          </span>
        </div>
      </div>

      <div className="brain-executive-summary-description">
        <ShieldCheck size={18} />

        <div>
          <span>
            Avaliação executiva
          </span>

          <strong>
            {prioridade.description}
          </strong>
        </div>
      </div>

      <div className="brain-executive-metrics-grid">
        <div className="brain-executive-metric">
          <div className="brain-executive-metric-icon brain-executive-metric-blue">
            <Sparkles size={18} />
          </div>

          <div>
            <span>
              Conhecimentos
            </span>

            <strong>
              {report.total}
            </strong>

            <small>
              Total produzido
            </small>
          </div>
        </div>

        <div className="brain-executive-metric">
          <div className="brain-executive-metric-icon brain-executive-metric-orange">
            <Clock3 size={18} />
          </div>

          <div>
            <span>
              Pendentes
            </span>

            <strong>
              {report.pending}
            </strong>

            <small>
              Aguardando ação
            </small>
          </div>
        </div>

        <div className="brain-executive-metric">
          <div className="brain-executive-metric-icon brain-executive-metric-red">
            <ShieldAlert size={18} />
          </div>

          <div>
            <span>
              Críticos
            </span>

            <strong>
              {report.critical}
            </strong>

            <small>
              Exigem prioridade
            </small>
          </div>
        </div>

        <div className="brain-executive-metric">
          <div className="brain-executive-metric-icon brain-executive-metric-green">
            <CheckCircle2 size={18} />
          </div>

          <div>
            <span>
              Resolvidos
            </span>

            <strong>
              {report.resolved}
            </strong>

            <small>
              Já tratados
            </small>
          </div>
        </div>
      </div>

      <div className="brain-executive-progress-grid">
        <div className="brain-executive-progress-card">
          <div className="brain-executive-progress-header">
            <span>
              Conhecimentos pendentes
            </span>

            <strong>
              {conhecimentosPendentes.toFixed(
                1,
              )}
              %
            </strong>
          </div>

          <div className="brain-executive-progress-track">
            <div
              className="brain-executive-progress-value brain-executive-progress-pending"
              style={{
                width: `${conhecimentosPendentes}%`,
              }}
            />
          </div>
        </div>

        <div className="brain-executive-progress-card">
          <div className="brain-executive-progress-header">
            <span>
              Conhecimentos resolvidos
            </span>

            <strong>
              {conhecimentosResolvidos.toFixed(
                1,
              )}
              %
            </strong>
          </div>

          <div className="brain-executive-progress-track">
            <div
              className="brain-executive-progress-value brain-executive-progress-resolved"
              style={{
                width: `${conhecimentosResolvidos}%`,
              }}
            />
          </div>
        </div>
      </div>

      <div className="brain-executive-agents-section">
        <div className="brain-executive-agents-header">
          <div>
            <span>
              Participação operacional
            </span>

            <h3>
              Especialidades envolvidas
            </h3>

            <p>
              Distribuição dos conhecimentos
              produzidos pelas áreas internas
              de análise.
            </p>
          </div>

          <strong>
            {totalAgentes}{" "}
            {totalAgentes === 1
              ? "especialidade"
              : "especialidades"}
          </strong>
        </div>

        <div className="brain-executive-agents-list">
          {report.agents.map(
            (agent) => {
              const participacao =
                percentual(
                  agent.knowledge,
                  report.total,
                );

              return (
                <div
                  key={agent.agent}
                  className="brain-executive-agent"
                >
                  <div className="brain-executive-agent-main">
                    <div className="brain-executive-agent-icon">
                      <ShieldCheck size={16} />
                    </div>

                    <div>
                      <strong>
                        {formatarNomeAgente(
                          agent.agent,
                        )}
                      </strong>

                      <span>
                        {agent.knowledge}{" "}
                        {agent.knowledge === 1
                          ? "conhecimento"
                          : "conhecimentos"}
                      </span>
                    </div>
                  </div>

                  <div className="brain-executive-agent-participation">
                    <span>
                      {participacao.toFixed(
                        1,
                      )}
                      %
                    </span>

                    <div className="brain-executive-agent-track">
                      <div
                        style={{
                          width: `${participacao}%`,
                        }}
                      />
                    </div>
                  </div>
                </div>
              );
            },
          )}
        </div>
      </div>

      {report.critical > 0 && (
        <div className="brain-executive-alert">
          <AlertTriangle size={19} />

          <div>
            <span>
              Atenção executiva
            </span>

            <strong>
              Existem {report.critical}{" "}
              {report.critical === 1
                ? "alerta crítico"
                : "alertas críticos"}{" "}
              que exigem acompanhamento
              imediato.
            </strong>
          </div>
        </div>
      )}
    </article>
  );
}