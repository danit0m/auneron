import {
  BadgeDollarSign,
  CalendarDays,
  Clock3,
  FileKey2,
  Landmark,
  UserRound,
} from "lucide-react";

import type {
  LatestDecisionResponse,
} from "../../types/orchestrator";

interface DecisionContextCardProps {
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
  if (!data) {
    return "Não informado";
  }

  const dataConvertida = new Date(
    `${data}T00:00:00`,
  );

  if (
    Number.isNaN(
      dataConvertida.getTime(),
    )
  ) {
    return data;
  }

  return new Intl.DateTimeFormat(
    "pt-BR",
    {
      dateStyle: "short",
    },
  ).format(dataConvertida);
}

function formatarDataHora(
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

function formatarStatus(
  status: string,
): string {
  return status
    .trim()
    .toLowerCase()
    .replace(
      /\b\w/g,
      (letra) => letra.toUpperCase(),
    );
}

function classeStatus(
  status: string,
): string {
  const statusNormalizado =
    status.trim().toLowerCase();

  if (statusNormalizado === "pago") {
    return "decision-context-status decision-context-status-paid";
  }

  if (
    statusNormalizado === "atrasado"
  ) {
    return "decision-context-status decision-context-status-late";
  }

  return "decision-context-status decision-context-status-open";
}

export default function DecisionContextCard({
  data,
}: DecisionContextCardProps) {
  if (
    !data?.available ||
    !data.decision
  ) {
    return (
      <article className="decision-context-card">
        <div className="decision-context-header">
          <div className="decision-context-title">
            <div className="decision-context-title-icon">
              <Landmark size={21} />
            </div>

            <div>
              <span>
                Decision Context
              </span>

              <h3>
                Contexto ainda não disponível
              </h3>
            </div>
          </div>
        </div>

        <div className="decision-context-empty">
          <Landmark size={36} />

          <strong>
            Nenhum contexto analisado
          </strong>

          <p>
            Os dados utilizados pelo Decision
            Engine aparecerão após uma nova
            decisão.
          </p>
        </div>
      </article>
    );
  }

  const decision = data.decision;
  const context = decision.context;

  return (
    <article className="decision-context-card">
      <div className="decision-context-header">
        <div className="decision-context-title">
          <div className="decision-context-title-icon">
            <Landmark size={21} />
          </div>

          <div>
            <span>
              Decision Context
            </span>

            <h3>
              Dados utilizados pela IA
            </h3>
          </div>
        </div>

        <span
          className={classeStatus(
            context.status,
          )}
        >
          {formatarStatus(context.status)}
        </span>
      </div>

      <p className="decision-context-description">
        Este painel apresenta o contexto
        financeiro considerado pelo Decision
        Engine para aplicar a regra e selecionar
        os agentes responsáveis pela análise.
      </p>

      <div className="decision-context-grid-v2">
        <div className="decision-context-item-v2 decision-context-item-wide">
          <div className="decision-context-item-icon context-icon-blue">
            <UserRound size={18} />
          </div>

          <div>
            <span>Cliente analisado</span>

            <strong>
              {context.cliente}
            </strong>
          </div>
        </div>

        <div className="decision-context-item-v2">
          <div className="decision-context-item-icon context-icon-green">
            <BadgeDollarSign size={18} />
          </div>

          <div>
            <span>Valor financeiro</span>

            <strong>
              {formatarMoeda(
                context.valor,
              )}
            </strong>
          </div>
        </div>

        <div className="decision-context-item-v2">
          <div className="decision-context-item-icon context-icon-orange">
            <CalendarDays size={18} />
          </div>

          <div>
            <span>Vencimento</span>

            <strong>
              {formatarData(
                context.vencimento,
              )}
            </strong>
          </div>
        </div>

        <div className="decision-context-item-v2">
          <div className="decision-context-item-icon context-icon-red">
            <Clock3 size={18} />
          </div>

          <div>
            <span>Dias em atraso</span>

            <strong>
              {context.dias_atraso}{" "}
              {context.dias_atraso === 1
                ? "dia"
                : "dias"}
            </strong>
          </div>
        </div>

        <div className="decision-context-item-v2 decision-context-item-wide">
          <div className="decision-context-item-icon context-icon-purple">
            <FileKey2 size={18} />
          </div>

          <div>
            <span>Regra aplicada</span>

            <strong>
              {formatarNome(
                decision.decision_name,
              )}
            </strong>
          </div>
        </div>
      </div>

      <div className="decision-context-audit">
        <div>
          <span>Evento analisado</span>

          <strong>
            {decision.event_name}
          </strong>
        </div>

        <div>
          <span>Confiança da decisão</span>

          <strong>
            {decision.confidence_percentage.toFixed(
              1,
            )}
            %
          </strong>
        </div>

        <div>
          <span>Decisão registrada em</span>

          <strong>
            {formatarDataHora(
              decision.created_at,
            )}
          </strong>
        </div>
      </div>

      <div className="decision-context-id">
        <span>Decision ID</span>

        <strong title={decision.decision_id}>
          {decision.decision_id}
        </strong>
      </div>
    </article>
  );
}