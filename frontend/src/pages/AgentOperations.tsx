import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock3,
  Cpu,
  RefreshCw,
  ServerCog,
  ShieldCheck,
  Workflow,
  XCircle,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import { Link } from "react-router-dom";

import api, {
  getApiErrorMessage,
} from "../api/api";
import { Header } from "../components/layout/Header";
import type {
  AgentMetrics,
  OrchestratorHealth,
  OrchestratorMetrics,
  OrchestratorRegistry,
  OrchestratorTelemetry,
  TelemetryRecord,
} from "../types/orchestrator";

import "./AgentOperations.css";

function formatarDuracao(
  segundos: number,
): string {
  if (segundos <= 0) {
    return "0 µs";
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

function formatarData(
  data: string,
): string {
  const date = new Date(data);

  if (Number.isNaN(date.getTime())) {
    return "Data não informada";
  }

  return new Intl.DateTimeFormat(
    "pt-BR",
    {
      dateStyle: "short",
      timeStyle: "medium",
    },
  ).format(date);
}

function classeStatus(
  status: string,
): string {
  return status === "SUCCESS"
    ? "agent-operation-status agent-operation-success"
    : "agent-operation-status agent-operation-error";
}

export default function AgentOperations() {
  const [health, setHealth] =
    useState<OrchestratorHealth | null>(
      null,
    );

  const [registry, setRegistry] =
    useState<OrchestratorRegistry | null>(
      null,
    );

  const [metrics, setMetrics] =
    useState<OrchestratorMetrics | null>(
      null,
    );

  const [telemetry, setTelemetry] =
    useState<OrchestratorTelemetry | null>(
      null,
    );

  const [carregando, setCarregando] =
    useState(true);

  const [atualizando, setAtualizando] =
    useState(false);

  const [erro, setErro] =
    useState("");

  const [filtroAgente, setFiltroAgente] =
    useState("todos");

  const [filtroStatus, setFiltroStatus] =
    useState("todos");

  const carregarDados = useCallback(
    async (
      mostrarCarregamento = true,
    ) => {
      try {
        if (mostrarCarregamento) {
          setCarregando(true);
        } else {
          setAtualizando(true);
        }

        setErro("");

        const [
          healthResponse,
          registryResponse,
          metricsResponse,
          telemetryResponse,
        ] = await Promise.all([
          api.get<OrchestratorHealth>(
            "/orchestrator/health",
          ),

          api.get<OrchestratorRegistry>(
            "/orchestrator/registry",
          ),

          api.get<OrchestratorMetrics>(
            "/orchestrator/metrics",
          ),

          api.get<OrchestratorTelemetry>(
            "/orchestrator/telemetry",
            {
              params: {
                limit: 500,
              },
            },
          ),

        ]);

        setHealth(healthResponse.data);
        setRegistry(
          registryResponse.data,
        );
        setMetrics(
          metricsResponse.data,
        );
        setTelemetry(
          telemetryResponse.data,
        );
      } catch (error) {
        console.error(
          "Erro ao carregar Agent Operations:",
          error,
        );

        setErro(
          getApiErrorMessage(
            error,
            "Não foi possível carregar as informações do Serviço de processamento.",
          ),
        );
      } finally {
        setCarregando(false);
        setAtualizando(false);
      }
    },
    [],
  );

  useEffect(() => {
    const timeoutId = window.setTimeout(
      () => {
        void carregarDados();
      },
      0,
    );

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [carregarDados]);

  const agentesRegistrados =
    useMemo(() => {
      if (!registry) {
        return [];
      }

      return Object.entries(
        registry.events,
      ).flatMap(
        ([eventName, agents]) =>
          agents.map((agent) => ({
            ...agent,
            eventName,
          })),
      );
    }, [registry]);

  const metricasOrdenadas =
    useMemo(() => {
      return [
        ...(metrics?.agents ?? []),
      ].sort(
        (a, b) =>
          a.agent_name.localeCompare(
            b.agent_name,
          ),
      );
    }, [metrics]);

  const telemetriaFiltrada =
    useMemo(() => {
      const registros =
        telemetry?.records ?? [];

      return registros.filter(
        (registro) => {
          const agenteCorresponde =
            filtroAgente === "todos" ||
            registro.agent_name ===
              filtroAgente;

          const statusCorresponde =
            filtroStatus === "todos" ||
            registro.status ===
              filtroStatus;

          return (
            agenteCorresponde &&
            statusCorresponde
          );
        },
      );
    }, [
      telemetry,
      filtroAgente,
      filtroStatus,
    ]);

  if (carregando) {
    return (
      <div className="page">
        <Header
          title="Saúde do sistema"
          subtitle="Disponibilidade, execuções e ocorrências do serviço"
        />

        <section className="page-content">
          <div className="state-container agent-operations-loading">
            <div className="loading-spinner" />

            <p>
              Carregando operações dos
              agentes...
            </p>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="page">
      <Header
        title="Saúde do sistema"
        subtitle="Disponibilidade, execuções e ocorrências do serviço"
      />

      <section className="page-content agent-operations-page">
        <div className="agent-operations-hero">
          <div>
            <span className="agent-operations-eyebrow">
              Administração do Auneron
            </span>

            <h2>
              Acompanhe a saúde do Auneron
            </h2>

            <p>
              Consulte a disponibilidade do serviço e as ocorrências que exigem atenção.
            </p>
          </div>

          <div className="agent-operations-hero-icon">
            <ServerCog size={34} />
          </div>
        </div>

        {erro && (
          <div className="agent-operations-error">
            <AlertTriangle size={20} />
            <span>{erro}</span>
          </div>
        )}

        <div className="agent-operations-actions">
          <div className="agent-orchestrator-status">
            <span
              className={
                !erro && health?.status ===
                "healthy"
                  ? "agent-status-dot agent-status-online"
                  : "agent-status-dot agent-status-offline"
              }
            />

            <div>
              <strong>
                Serviço de processamento
              </strong>

              <span>
                {!erro && health?.status ===
                "healthy"
                  ? "Operacional"
                  : "Sem confirmação de disponibilidade"}
              </span>
            </div>
          </div>

          <button
            type="button"
            className="secondary-button"
            disabled={atualizando}
            onClick={() =>
              void carregarDados(false)
            }
          >
            <RefreshCw
              size={17}
              className={
                atualizando
                  ? "rotating-icon"
                  : ""
              }
            />

            {atualizando
              ? "Atualizando..."
              : "Atualizar"}
          </button>
        </div>

        <div className="agent-operations-summary">


          <article className="agent-operation-summary-card">
            <div className="agent-operation-summary-icon agent-operation-blue">
              <Activity size={21} />
            </div>

            <div>
              <span>Execuções</span>

              <strong>
                {erro || !health ? "Indisponível" : health.executions}
              </strong>

              <small>
                Desde a inicialização
              </small>
            </div>
          </article>

          <article className="agent-operation-summary-card">
            <div className="agent-operation-summary-icon agent-operation-green">
              <CheckCircle2 size={21} />
            </div>

            <div>
              <span>
                Taxa de sucesso
              </span>

              <strong>
                {erro || !health ? "Indisponível" : health.executions > 0 ? `${health.success_rate.toFixed(1)}%` : "Sem medições"}
              </strong>

              <small>
                Desde a inicialização do serviço
              </small>
            </div>
          </article>

          <article className="agent-operation-summary-card">
            <div className="agent-operation-summary-icon agent-operation-orange">
              <Clock3 size={21} />
            </div>

            <div>
              <span>Tempo médio</span>

              <strong>
                {erro || !metrics ? "Indisponível" : metrics.summary.executions > 0 ? formatarDuracao(metrics.summary.average_duration_seconds) : "Sem medições"}
              </strong>

              <small>Desde a inicialização do serviço</small>
            </div>
          </article>

          <article className="agent-operation-summary-card">
            <div className="agent-operation-summary-icon agent-operation-red">
              <XCircle size={21} />
            </div>

            <div>
              <span>Falhas</span>

              <strong>
                {erro || !health ? "Indisponível" : health.failures}
              </strong>

              <small>
                Erros registrados
              </small>
            </div>
          </article>
        </div>

        <section className="system-health-notices" aria-labelledby="system-health-notices-title">
          <h2 id="system-health-notices-title">Ocorrências que exigem atenção</h2>
          {erro || !health ? (
            <p>Não foi possível confirmar o estado do serviço. Tente atualizar a consulta.</p>
          ) : health.failures > 0 ? (
            <p>Há {health.failures} falhas registradas desde a inicialização do serviço. Consulte o diagnóstico técnico para investigar.</p>
          ) : (
            <p>Nenhuma falha de execução registrada desde a inicialização do serviço.</p>
          )}
          <Link to="/inteligencia">Consultar análises e recomendações na Central de Inteligência</Link>
        </section>

        <details className="system-health-diagnostics">
          <summary>Diagnóstico técnico</summary>
          <p>Agentes configurados, desempenho e registros recentes de execução para administração e suporte.</p>
        <div className="agent-operations-grid">
          <section className="panel agent-registry-panel">
            <div className="agent-operation-panel-header">
              <div>
                <span>
                  <Workflow size={19} />
                </span>

                <div>
                  <h3>
                    Agentes configurados
                  </h3>

                  <p>
                    Ordem e prioridade de
                    execução.
                  </p>
                </div>
              </div>
            </div>

            <div className="agent-registry-list">
              {agentesRegistrados.map(
                (agent) => (
                  <article
                    key={`${agent.eventName}-${agent.name}`}
                    className="agent-registry-item"
                  >
                    <div className="agent-registry-position">
                      {agent.priority}
                    </div>

                    <div>
                      <strong>
                        {agent.name}
                      </strong>

                      <span>
                        Evento:{" "}
                        {agent.eventName}
                      </span>
                    </div>

                    <ShieldCheck size={18} />
                  </article>
                ),
              )}
            </div>
          </section>

          <section className="panel agent-metrics-panel">
            <div className="agent-operation-panel-header">
              <div>
                <span>
                  <Cpu size={19} />
                </span>

                <div>
                  <h3>Desempenho</h3>

                  <p>
                    Métricas acumuladas por
                    agente.
                  </p>
                </div>
              </div>
            </div>

            <div className="agent-metrics-table-wrapper">
              <table className="agent-metrics-table">
                <thead>
                  <tr>
                    <th>Agente</th>
                    <th>Execuções</th>
                    <th>Sucesso</th>
                    <th>Tempo médio</th>
                    <th>Falhas</th>
                  </tr>
                </thead>

                <tbody>
                  {metricasOrdenadas.length ===
                  0 ? (
                    <tr>
                      <td
                        colSpan={5}
                        className="agent-empty-table"
                      >
                        Nenhuma métrica
                        registrada nesta
                        inicialização.
                      </td>
                    </tr>
                  ) : (
                    metricasOrdenadas.map(
                      (
                        metric: AgentMetrics,
                      ) => (
                        <tr
                          key={
                            metric.agent_name
                          }
                        >
                          <td>
                            <div className="agent-metric-name">
                              <Bot size={16} />

                              <strong>
                                {
                                  metric.agent_name
                                }
                              </strong>
                            </div>
                          </td>

                          <td>
                            {metric.executions}
                          </td>

                          <td>
                            <span className="agent-success-rate">
                              {metric.executions > 0 ? `${metric.success_rate.toFixed(1)}%` : "Sem medições"}
                            </span>
                          </td>

                          <td>
                            {metric.executions > 0 ? formatarDuracao(metric.average_duration_seconds) : "Sem medições"}
                          </td>

                          <td>
                            <span
                              className={
                                metric.failures >
                                0
                                  ? "agent-failure-count agent-failure-active"
                                  : "agent-failure-count"
                              }
                            >
                              {
                                metric.failures
                              }
                            </span>
                          </td>
                        </tr>
                      ),
                    )
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <section className="panel agent-telemetry-panel">
          <div className="agent-telemetry-toolbar">
            <div className="agent-operation-panel-header agent-telemetry-title">
              <div>
                <span>
                  <Activity size={19} />
                </span>

                <div>
                  <h3>
                    Telemetria recente
                  </h3>

                  <p>
                    Histórico das últimas
                    execuções.
                  </p>
                </div>
              </div>
            </div>

            <div className="agent-telemetry-filters">
              <select
                value={filtroAgente}
                aria-label="Filtrar por agente"
                onChange={(event) =>
                  setFiltroAgente(
                    event.target.value,
                  )
                }
              >
                <option value="todos">
                  Todos os agentes
                </option>

                {agentesRegistrados.map(
                  (agent) => (
                    <option
                      key={`${agent.eventName}-${agent.name}`}
                      value={agent.name}
                    >
                      {agent.name}
                    </option>
                  ),
                )}
              </select>

              <select
                value={filtroStatus}
                aria-label="Filtrar por status"
                onChange={(event) =>
                  setFiltroStatus(
                    event.target.value,
                  )
                }
              >
                <option value="todos">
                  Todos os status
                </option>

                <option value="SUCCESS">
                  Sucesso
                </option>

                <option value="ERROR">
                  Erro
                </option>
              </select>
            </div>
          </div>

          <div className="agent-telemetry-count">
            Exibindo{" "}

            <strong>
              {telemetriaFiltrada.length}
            </strong>{" "}

            registros
          </div>

          {telemetriaFiltrada.length ===
          0 ? (
            <div className="agent-operations-empty">
              <Activity size={32} />

              <h3>
                Nenhuma execução encontrada
              </h3>

              <p>
                Os registros aparecerão quando houver execuções correspondentes aos filtros.
              </p>
            </div>
          ) : (
            <div className="agent-telemetry-list">
              {telemetriaFiltrada.map(
                (
                  record: TelemetryRecord,
                ) => (
                  <article
                    key={
                      record.telemetry_id
                    }
                    className="agent-telemetry-item"
                  >
                    <div
                      className={
                        record.status ===
                        "SUCCESS"
                          ? "agent-telemetry-icon agent-telemetry-icon-success"
                          : "agent-telemetry-icon agent-telemetry-icon-error"
                      }
                    >
                      {record.status ===
                      "SUCCESS" ? (
                        <CheckCircle2
                          size={18}
                        />
                      ) : (
                        <XCircle size={18} />
                      )}
                    </div>

                    <div className="agent-telemetry-main">
                      <div className="agent-telemetry-heading">
                        <div>
                          <strong>
                            {
                              record.agent_name
                            }
                          </strong>

                          <span>
                            {
                              record.strategy_name
                            }
                          </span>
                        </div>

                        <span
                          className={classeStatus(
                            record.status,
                          )}
                        >
                          {record.status ===
                          "SUCCESS"
                            ? "Sucesso"
                            : "Erro"}
                        </span>
                      </div>

                      <div className="agent-telemetry-meta">
                        <span>
                          Evento:{" "}
                          <strong>
                            {
                              record.event_name
                            }
                          </strong>
                        </span>

                        <span>
                          Prioridade:{" "}
                          <strong>
                            {
                              record.priority
                            }
                          </strong>
                        </span>

                        <span>
                          Duração:{" "}
                          <strong>
                            {formatarDuracao(
                              record.duration_seconds,
                            )}
                          </strong>
                        </span>

                        <span>
                          {formatarData(
                            record.started_at,
                          )}
                        </span>
                      </div>

                      {record.error_message && (
                        <div className="agent-telemetry-error-message">
                          <AlertTriangle
                            size={15}
                          />

                          <span>
                            {
                              record.error_type
                            }
                            :{" "}
                            {
                              record.error_message
                            }
                          </span>
                        </div>
                      )}
                    </div>
                  </article>
                ),
              )}
            </div>
          )}
        </section>
        </details>
      </section>
    </div>
  );
}
