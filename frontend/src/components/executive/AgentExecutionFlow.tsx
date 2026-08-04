import {
  Activity,
  Bot,
  CheckCircle2,
  Clock3,
  Workflow,
  XCircle,
} from "lucide-react";

import type {
  LatestDecisionResponse,
  OrchestratorTelemetry,
  TelemetryRecord,
} from "../../types/orchestrator";

interface AgentExecutionFlowProps {
  decisionData: LatestDecisionResponse | null;
  telemetryData: OrchestratorTelemetry | null;
}

interface AgentExecutionStep {
  agentName: string;
  order: number;
  priority: number | null;
  status: "SUCCESS" | "ERROR" | "PENDING";
  durationSeconds: number | null;
  startedAt: string | null;
  finishedAt: string | null;
  errorMessage: string | null;
}

function formatarDuracao(
  segundos: number | null,
): string {
  if (
    segundos === null ||
    segundos < 0
  ) {
    return "Aguardando";
  }

  if (segundos < 0.001) {
    return `${(
      segundos * 1_000_000
    ).toFixed(0)} µs`;
  }

  if (segundos < 1) {
    return `${(
      segundos * 1000
    ).toFixed(1)} ms`;
  }

  return `${segundos.toFixed(2)} s`;
}

function formatarDataHora(
  data: string | null,
): string {
  if (!data) {
    return "Não registrado";
  }

  const dataConvertida = new Date(data);

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

function obterTelemetriaAgente(
  records: TelemetryRecord[],
  agentName: string,
  strategyName: string,
): TelemetryRecord | null {
  const registrosDoAgente =
    records.filter(
      (record) =>
        record.agent_name ===
          agentName &&
        record.strategy_name ===
          strategyName,
    );

  if (
    registrosDoAgente.length === 0
  ) {
    return null;
  }

  return [...registrosDoAgente].sort(
    (a, b) =>
      new Date(
        b.started_at,
      ).getTime() -
      new Date(
        a.started_at,
      ).getTime(),
  )[0];
}

function montarEtapas(
  decisionData: LatestDecisionResponse,
  telemetryData: OrchestratorTelemetry | null,
): AgentExecutionStep[] {
  if (
    !decisionData.available ||
    !decisionData.decision
  ) {
    return [];
  }

  const decision =
    decisionData.decision;

  const records =
    telemetryData?.records ?? [];

  return decision.selected_agents.map(
    (agentName, index) => {
      const telemetryRecord =
        obterTelemetriaAgente(
          records,
          agentName,
          decision.decision_name,
        );

      return {
        agentName,
        order: index + 1,
        priority:
          telemetryRecord?.priority ??
          null,
        status:
          telemetryRecord?.status ??
          "PENDING",
        durationSeconds:
          telemetryRecord
            ?.duration_seconds ??
          null,
        startedAt:
          telemetryRecord
            ?.started_at ??
          null,
        finishedAt:
          telemetryRecord
            ?.finished_at ??
          null,
        errorMessage:
          telemetryRecord
            ?.error_message ??
          null,
      };
    },
  );
}

function classeStatus(
  status: AgentExecutionStep["status"],
): string {
  if (status === "SUCCESS") {
    return "agent-execution-status agent-execution-status-success";
  }

  if (status === "ERROR") {
    return "agent-execution-status agent-execution-status-error";
  }

  return "agent-execution-status agent-execution-status-pending";
}

function textoStatus(
  status: AgentExecutionStep["status"],
): string {
  if (status === "SUCCESS") {
    return "Concluído";
  }

  if (status === "ERROR") {
    return "Falha";
  }

  return "Aguardando";
}

function iconeStatus(
  status: AgentExecutionStep["status"],
) {
  if (status === "SUCCESS") {
    return (
      <CheckCircle2 size={18} />
    );
  }

  if (status === "ERROR") {
    return <XCircle size={18} />;
  }

  return <Clock3 size={18} />;
}

export default function AgentExecutionFlow({
  decisionData,
  telemetryData,
}: AgentExecutionFlowProps) {
  if (
    !decisionData?.available ||
    !decisionData.decision
  ) {
    return (
      <article className="agent-execution-flow-card">
        <div className="agent-execution-flow-header">
          <div className="agent-execution-flow-title">
            <div className="agent-execution-flow-title-icon">
              <Workflow size={22} />
            </div>

            <div>
              <span>
                Agent Execution Flow
              </span>

              <h3>
                Fluxo ainda não disponível
              </h3>
            </div>
          </div>
        </div>

        <div className="agent-execution-flow-empty">
          <Workflow size={38} />

          <strong>
            Nenhuma execução registrada
          </strong>

          <p>
            O fluxo dos agentes será exibido
            depois que o Decision Engine
            produzir uma nova decisão.
          </p>
        </div>
      </article>
    );
  }

  const decision =
    decisionData.decision;

  const etapas = montarEtapas(
    decisionData,
    telemetryData,
  );

  const concluidos =
    etapas.filter(
      (etapa) =>
        etapa.status === "SUCCESS",
    ).length;

  const falhas =
    etapas.filter(
      (etapa) =>
        etapa.status === "ERROR",
    ).length;

  const tempoTotal =
    etapas.reduce(
      (total, etapa) =>
        total +
        (
          etapa.durationSeconds ??
          0
        ),
      0,
    );

  return (
    <article className="agent-execution-flow-card">
      <div className="agent-execution-flow-header">
        <div className="agent-execution-flow-title">
          <div className="agent-execution-flow-title-icon">
            <Workflow size={22} />
          </div>

          <div>
            <span>
              Agent Execution Flow
            </span>

            <h3>
              Fluxo de execução dos agentes
            </h3>
          </div>
        </div>

        <span className="agent-execution-flow-strategy">
          {formatarNome(
            decision.decision_name,
          )}
        </span>
      </div>

      <div className="agent-execution-flow-summary">
        <div>
          <Activity size={17} />

          <span>
            Agentes acionados
          </span>

          <strong>
            {etapas.length}
          </strong>
        </div>

        <div>
          <CheckCircle2 size={17} />

          <span>
            Concluídos
          </span>

          <strong>
            {concluidos}
          </strong>
        </div>

        <div>
          <XCircle size={17} />

          <span>Falhas</span>

          <strong>
            {falhas}
          </strong>
        </div>

        <div>
          <Clock3 size={17} />

          <span>
            Tempo acumulado
          </span>

          <strong>
            {formatarDuracao(
              tempoTotal,
            )}
          </strong>
        </div>
      </div>

      <div className="agent-execution-flow-list">
        {etapas.map(
          (etapa, index) => (
            <div
              key={etapa.agentName}
              className="agent-execution-flow-wrapper"
            >
              <article className="agent-execution-step">
                <div className="agent-execution-order">
                  {etapa.order}
                </div>

                <div className="agent-execution-icon">
                  <Bot size={19} />
                </div>

                <div className="agent-execution-content">
                  <div className="agent-execution-heading">
                    <div>
                      <strong>
                        {etapa.agentName}
                      </strong>

                      <span>
                        Prioridade:{" "}
                        {etapa.priority ??
                          "Não registrada"}
                      </span>
                    </div>

                    <span
                      className={classeStatus(
                        etapa.status,
                      )}
                    >
                      {iconeStatus(
                        etapa.status,
                      )}

                      {textoStatus(
                        etapa.status,
                      )}
                    </span>
                  </div>

                  <div className="agent-execution-progress">
                    <div
                      className={
                        etapa.status ===
                        "SUCCESS"
                          ? "agent-execution-progress-value agent-execution-progress-success"
                          : etapa.status ===
                              "ERROR"
                            ? "agent-execution-progress-value agent-execution-progress-error"
                            : "agent-execution-progress-value agent-execution-progress-pending"
                      }
                    />
                  </div>

                  <div className="agent-execution-meta">
                    <span>
                      Duração:{" "}
                      <strong>
                        {formatarDuracao(
                          etapa.durationSeconds,
                        )}
                      </strong>
                    </span>

                    <span>
                      Início:{" "}
                      <strong>
                        {formatarDataHora(
                          etapa.startedAt,
                        )}
                      </strong>
                    </span>

                    <span>
                      Término:{" "}
                      <strong>
                        {formatarDataHora(
                          etapa.finishedAt,
                        )}
                      </strong>
                    </span>
                  </div>

                  {etapa.errorMessage && (
                    <div className="agent-execution-error">
                      <XCircle size={15} />

                      <span>
                        {etapa.errorMessage}
                      </span>
                    </div>
                  )}
                </div>
              </article>

              {index <
                etapas.length - 1 && (
                <div className="agent-execution-connector">
                  <span />
                  <ChevronDownIcon />
                  <span />
                </div>
              )}
            </div>
          ),
        )}
      </div>

      <div className="agent-execution-flow-footer">
        <span>
          Decision ID
        </span>

        <strong
          title={
            decision.decision_id
          }
        >
          {decision.decision_id}
        </strong>
      </div>
    </article>
  );
}

function ChevronDownIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M6 9L12 15L18 9"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}