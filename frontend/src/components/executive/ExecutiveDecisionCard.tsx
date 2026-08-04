import {
  BadgeCheck,
  Bot,
  BrainCircuit,
  CalendarClock,
  FileKey2,
  Gauge,
  UserRound,
  WalletCards,
} from "lucide-react";

import type {
  LatestDecisionResponse,
} from "../../types/orchestrator";

import "../../styles/executive.css";

interface ExecutiveDecisionCardProps {
  data: LatestDecisionResponse | null;
}

function formatarMoeda(
  valor: number,
): string {
  return new Intl.NumberFormat(
    "pt-BR",
    {
      style: "currency",
      currency: "BRL",
    },
  ).format(valor);
}

function formatarData(
  data: string,
): string {
  const dataConvertida = new Date(data);

  if (
    Number.isNaN(
      dataConvertida.getTime(),
    )
  ) {
    return "Data não informada";
  }

  return new Intl.DateTimeFormat(
    "pt-BR",
    {
      dateStyle: "short",
      timeStyle: "medium",
    },
  ).format(dataConvertida);
}

function formatarNome(
  nome: string,
): string {
  return nome
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(
      /\b\w/g,
      (letra) => letra.toUpperCase(),
    );
}

function classificarConfianca(
  percentual: number,
): string {
  if (percentual >= 95) {
    return "Muito alta";
  }

  if (percentual >= 85) {
    return "Alta";
  }

  if (percentual >= 70) {
    return "Moderada";
  }

  return "Baixa";
}

function classeConfianca(
  percentual: number,
): string {
  if (percentual >= 95) {
    return "executive-confidence executive-confidence-excellent";
  }

  if (percentual >= 85) {
    return "executive-confidence executive-confidence-high";
  }

  if (percentual >= 70) {
    return "executive-confidence executive-confidence-medium";
  }

  return "executive-confidence executive-confidence-low";
}

export default function ExecutiveDecisionCard({
  data,
}: ExecutiveDecisionCardProps) {
  if (
    !data?.available ||
    !data.decision
  ) {
    return (
      <article className="executive-decision-card">
        <div className="executive-decision-header">
          <div className="executive-decision-title">
            <div className="executive-decision-title-icon">
              <BrainCircuit size={23} />
            </div>

            <div>
              <span>
                Executive Decision
              </span>

              <h3>
                Nenhuma decisão disponível
              </h3>
            </div>
          </div>
        </div>

        <div className="executive-decision-empty">
          <BrainCircuit size={40} />

          <strong>
            O Decision Engine ainda não
            produziu uma análise
          </strong>

          <p>
            Cadastre um novo cliente para
            gerar uma decisão explicável.
          </p>
        </div>
      </article>
    );
  }

  const decision = data.decision;

  const nivelConfianca =
    classificarConfianca(
      decision.confidence_percentage,
    );

  return (
    <article className="executive-decision-card">
      <div className="executive-decision-header">
        <div className="executive-decision-title">
          <div className="executive-decision-title-icon">
            <BrainCircuit size={23} />
          </div>

          <div>
            <span>
              Executive Decision
            </span>

            <h3>
              {formatarNome(
                decision.decision_name,
              )}
            </h3>
          </div>
        </div>

        <div
          className={classeConfianca(
            decision.confidence_percentage,
          )}
        >
          <Gauge size={18} />

          <div>
            <strong>
              {decision.confidence_percentage.toFixed(
                1,
              )}
              %
            </strong>

            <span>
              Confiança {nivelConfianca}
            </span>
          </div>
        </div>
      </div>

      <div className="executive-decision-reason">
        <span>Motivo da decisão</span>

        <p>{decision.reason}</p>
      </div>

      <div className="executive-decision-grid">
        <div className="executive-decision-item">
          <div className="executive-decision-item-icon executive-icon-blue">
            <UserRound size={18} />
          </div>

          <div>
            <span>Cliente</span>

            <strong>
              {decision.context.cliente}
            </strong>
          </div>
        </div>

        <div className="executive-decision-item">
          <div className="executive-decision-item-icon executive-icon-green">
            <WalletCards size={18} />
          </div>

          <div>
            <span>Valor analisado</span>

            <strong>
              {formatarMoeda(
                decision.context.valor,
              )}
            </strong>
          </div>
        </div>

        <div className="executive-decision-item">
          <div className="executive-decision-item-icon executive-icon-orange">
            <BadgeCheck size={18} />
          </div>

          <div>
            <span>Status financeiro</span>

            <strong className="executive-capitalize">
              {decision.context.status}
            </strong>
          </div>
        </div>

        <div className="executive-decision-item">
          <div className="executive-decision-item-icon executive-icon-purple">
            <Bot size={18} />
          </div>

          <div>
            <span>Agentes acionados</span>

            <strong>
              {
                decision.selected_agents
                  .length
              }
            </strong>
          </div>
        </div>
      </div>

      <div className="executive-decision-metadata">
        <div>
          <FileKey2 size={15} />

          <span>Decision ID</span>

          <strong title={decision.decision_id}>
            {decision.decision_id}
          </strong>
        </div>

        <div>
          <CalendarClock size={15} />

          <span>Data e hora</span>

          <strong>
            {formatarData(
              decision.created_at,
            )}
          </strong>
        </div>
      </div>

      <div className="executive-confidence-progress">
        <div className="executive-confidence-progress-header">
          <span>
            Nível de confiança da decisão
          </span>

          <strong>
            {nivelConfianca}
          </strong>
        </div>

        <div className="executive-confidence-progress-track">
          <div
            className="executive-confidence-progress-value"
            style={{
              width: `${Math.min(
                Math.max(
                  decision.confidence_percentage,
                  0,
                ),
                100,
              )}%`,
            }}
          />
        </div>
      </div>
    </article>
  );
}