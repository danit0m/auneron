import {
  AlertTriangle,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import api, {
  getApiErrorMessage,
} from "../api/api";
import BrainExecutiveSummary from "../components/brain/BrainExecutiveSummary";
import { Header } from "../components/layout/Header";
import type { Knowledge } from "../types/knowledge";

import "../styles/brain.css";

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

function formatarData(data: string): string {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(data));
}

function classeSeveridade(
  severidade: string,
): string {
  const valor = severidade.toLowerCase();

  if (valor === "critical") {
    return (
      "brain-severity " +
      "brain-severity-critical"
    );
  }

  if (valor === "high") {
    return (
      "brain-severity " +
      "brain-severity-high"
    );
  }

  if (valor === "medium") {
    return (
      "brain-severity " +
      "brain-severity-medium"
    );
  }

  return (
    "brain-severity " +
    "brain-severity-info"
  );
}

function tituloSeveridade(
  severidade: string,
): string {
  const valor = severidade.toLowerCase();

  const traducoes: Record<string, string> = {
    critical: "Crítica",
    high: "Alta",
    medium: "Média",
    low: "Baixa",
    info: "Informativa",
  };

  return traducoes[valor] || severidade;
}

export default function Brain() {
  const [conhecimentos, setConhecimentos] =
    useState<Knowledge[]>([]);

  const [
    executiveReport,
    setExecutiveReport,
  ] = useState<ExecutiveReport | null>(null);

  const [carregando, setCarregando] =
    useState(true);

  const [atualizando, setAtualizando] =
    useState(false);

  const [erro, setErro] = useState("");

  const [pesquisa, setPesquisa] =
    useState("");

  const [filtroAgente, setFiltroAgente] =
    useState("todos");

  const [
    filtroSeveridade,
    setFiltroSeveridade,
  ] = useState("todas");

  const [filtroStatus, setFiltroStatus] =
    useState("pendentes");

  // =====================================
  // CARREGAR CONHECIMENTOS
  // =====================================

  const carregarConhecimentos = useCallback(
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

        const response =
          await api.get<Knowledge[]>(
            "/brain/",
            {
              params: {
                skip: 0,
                limit: 500,
              },
            },
          );

        setConhecimentos(response.data);
      } catch (error) {
        console.error(
          "Erro ao carregar conhecimentos:",
          error,
        );

        setErro(
          getApiErrorMessage(
            error,
            "Não foi possível carregar o Brain.",
          ),
        );
      } finally {
        setCarregando(false);
        setAtualizando(false);
      }
    },
    [],
  );

  // =====================================
  // CARREGAR RESUMO EXECUTIVO
  // =====================================

  const carregarExecutive =
    useCallback(async () => {
      try {
        const response =
          await api.get<ExecutiveReport>(
            "/brain/executive",
          );

        setExecutiveReport(response.data);
      } catch (error) {
        console.error(
          "Erro ao carregar Executive AI:",
          error,
        );
      }
    }, []);

  // =====================================
  // CARREGAMENTO INICIAL
  // =====================================

  useEffect(() => {
    const timeoutId = window.setTimeout(
      () => {
        void carregarConhecimentos();
        void carregarExecutive();
      },
      0,
    );

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [
    carregarConhecimentos,
    carregarExecutive,
  ]);

  // =====================================
  // LISTA DE AGENTES
  // =====================================

  const agentes = useMemo(() => {
    return Array.from(
      new Set(
        conhecimentos.map(
          (conhecimento) =>
            conhecimento.agent_name,
        ),
      ),
    ).sort();
  }, [conhecimentos]);

  // =====================================
  // FILTROS
  // =====================================

  const conhecimentosFiltrados =
    useMemo(() => {
      const termo = pesquisa
        .trim()
        .toLowerCase();

      return conhecimentos.filter(
        (conhecimento) => {
          const correspondePesquisa =
            !termo ||
            conhecimento.title
              .toLowerCase()
              .includes(termo) ||
            conhecimento.message
              .toLowerCase()
              .includes(termo) ||
            conhecimento.agent_name
              .toLowerCase()
              .includes(termo);

          const correspondeAgente =
            filtroAgente === "todos" ||
            conhecimento.agent_name ===
              filtroAgente;

          const correspondeSeveridade =
            filtroSeveridade === "todas" ||
            conhecimento.severity
              .toLowerCase() ===
              filtroSeveridade;

          const correspondeStatus =
            filtroStatus === "todos" ||
            (filtroStatus ===
            "resolvidos"
              ? conhecimento.resolved
              : !conhecimento.resolved);

          return (
            correspondePesquisa &&
            correspondeAgente &&
            correspondeSeveridade &&
            correspondeStatus
          );
        },
      );
    }, [
      conhecimentos,
      pesquisa,
      filtroAgente,
      filtroSeveridade,
      filtroStatus,
    ]);

  // =====================================
  // RESUMO LOCAL
  // =====================================

  const resumo = useMemo(() => {
    const pendentes =
      conhecimentos.filter(
        (item) => !item.resolved,
      ).length;

    const resolvidos =
      conhecimentos.filter(
        (item) => item.resolved,
      ).length;

    const criticos =
      conhecimentos.filter(
        (item) =>
          item.severity.toLowerCase() ===
            "critical" &&
          !item.resolved,
      ).length;

    const agentesAtivos = new Set(
      conhecimentos.map(
        (item) => item.agent_name,
      ),
    ).size;

    return {
      total: conhecimentos.length,
      pendentes,
      resolvidos,
      criticos,
      agentesAtivos,
    };
  }, [conhecimentos]);

  // =====================================
  // RESOLVER OU REABRIR CONHECIMENTO
  // =====================================

  async function alternarResolvido(
    conhecimento: Knowledge,
  ) {
    try {
      const endpoint =
        conhecimento.resolved
          ? `/brain/${conhecimento.id}/reopen`
          : `/brain/${conhecimento.id}/resolve`;

      await api.patch(endpoint);

      setConhecimentos(
        (estadoAtual) =>
          estadoAtual.map((item) =>
            item.id === conhecimento.id
              ? {
                  ...item,
                  resolved:
                    !item.resolved,
                }
              : item,
          ),
      );

      await carregarExecutive();
    } catch (error) {
      console.error(
        "Erro ao atualizar conhecimento:",
        error,
      );

      setErro(
        getApiErrorMessage(
          error,
          "Não foi possível atualizar o conhecimento.",
        ),
      );
    }
  }

  // =====================================
  // ATUALIZAR BRAIN COMPLETO
  // =====================================

  async function atualizarBrain() {
    await Promise.all([
      carregarConhecimentos(false),
      carregarExecutive(),
    ]);
  }

  // =====================================
  // RENDERIZAÇÃO
  // =====================================

  return (
    <div className="page">
      <Header
        title="Brain Center"
        subtitle="Acompanhe os conhecimentos produzidos pelos agentes"
      />

      <section className="page-content brain-page">
        <div className="brain-hero">
          <div>
            <span className="brain-eyebrow">
              Inteligência operacional
            </span>

            <h2>
              O cérebro do Auneron AI
            </h2>

            <p>
              Visualize alertas, análises
              e recomendações geradas pelos
              agentes em tempo real.
            </p>
          </div>

          <div className="brain-hero-icon">
            <BrainCircuit size={34} />
          </div>
        </div>

        <BrainExecutiveSummary
          report={executiveReport}
        />

        <div className="brain-summary-grid">
          <article className="brain-summary-card">
            <div className="brain-summary-icon brain-blue">
              <Sparkles size={21} />
            </div>

            <div>
              <span>Conhecimentos</span>

              <strong>
                {resumo.total}
              </strong>

              <small>
                Total produzido
              </small>
            </div>
          </article>

          <article className="brain-summary-card">
            <div className="brain-summary-icon brain-orange">
              <Clock3 size={21} />
            </div>

            <div>
              <span>Pendentes</span>

              <strong>
                {resumo.pendentes}
              </strong>

              <small>
                Aguardando ação
              </small>
            </div>
          </article>

          <article className="brain-summary-card">
            <div className="brain-summary-icon brain-red">
              <ShieldAlert size={21} />
            </div>

            <div>
              <span>Críticos</span>

              <strong>
                {resumo.criticos}
              </strong>

              <small>
                Exigem prioridade
              </small>
            </div>
          </article>

          <article className="brain-summary-card">
            <div className="brain-summary-icon brain-green">
              <CheckCircle2 size={21} />
            </div>

            <div>
              <span>Resolvidos</span>

              <strong>
                {resumo.resolvidos}
              </strong>

              <small>
                Já tratados
              </small>
            </div>
          </article>

          <article className="brain-summary-card">
            <div className="brain-summary-icon brain-purple">
              <Bot size={21} />
            </div>

            <div>
              <span>Agentes ativos</span>

              <strong>
                {resumo.agentesAtivos}
              </strong>

              <small>
                Produzindo análises
              </small>
            </div>
          </article>
        </div>

        <section className="panel brain-panel">
          <div className="brain-toolbar">
            <div className="brain-search">
              <Search size={18} />

              <input
                type="search"
                value={pesquisa}
                placeholder="Pesquisar conhecimentos..."
                aria-label="Pesquisar conhecimentos"
                onChange={(event) =>
                  setPesquisa(
                    event.target.value,
                  )
                }
              />
            </div>

            <div className="brain-filters">
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

                {agentes.map(
                  (agente) => (
                    <option
                      key={agente}
                      value={agente}
                    >
                      {agente}
                    </option>
                  ),
                )}
              </select>

              <select
                value={filtroSeveridade}
                aria-label="Filtrar por severidade"
                onChange={(event) =>
                  setFiltroSeveridade(
                    event.target.value,
                  )
                }
              >
                <option value="todas">
                  Todas as severidades
                </option>

                <option value="critical">
                  Crítica
                </option>

                <option value="high">
                  Alta
                </option>

                <option value="medium">
                  Média
                </option>

                <option value="info">
                  Informativa
                </option>
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
                <option value="pendentes">
                  Pendentes
                </option>

                <option value="resolvidos">
                  Resolvidos
                </option>

                <option value="todos">
                  Todos
                </option>
              </select>

              <button
                type="button"
                className="secondary-button"
                disabled={atualizando}
                onClick={() =>
                  void atualizarBrain()
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
          </div>

          <div className="brain-results-info">
            Exibindo{" "}

            <strong>
              {
                conhecimentosFiltrados.length
              }
            </strong>{" "}

            de{" "}

            <strong>
              {conhecimentos.length}
            </strong>{" "}

            conhecimentos
          </div>

          {carregando ? (
            <div className="state-container brain-loading">
              <div className="loading-spinner" />

              <p>
                Carregando o Brain...
              </p>
            </div>
          ) : erro ? (
            <div className="brain-error-state">
              <AlertTriangle size={22} />

              <span>{erro}</span>
            </div>
          ) : conhecimentosFiltrados
              .length === 0 ? (
            <div className="brain-empty-state">
              <BrainCircuit size={34} />

              <h3>
                Nenhum conhecimento
                encontrado
              </h3>

              <p>
                Cadastre um cliente de alto
                valor ou em atraso para gerar
                novos conhecimentos.
              </p>
            </div>
          ) : (
            <div className="brain-timeline">
              {conhecimentosFiltrados.map(
                (conhecimento) => (
                  <article
                    key={conhecimento.id}
                    className={
                      `brain-knowledge-card ${
                        conhecimento.resolved
                          ? "brain-knowledge-resolved"
                          : ""
                      }`
                    }
                  >
                    <div className="brain-timeline-marker">
                      <Bot size={18} />
                    </div>

                    <div className="brain-knowledge-content">
                      <div className="brain-knowledge-header">
                        <div>
                          <span className="brain-agent-name">
                            {
                              conhecimento.agent_name
                            }
                          </span>

                          <h3>
                            {
                              conhecimento.title
                            }
                          </h3>
                        </div>

                        <span
                          className={
                            classeSeveridade(
                              conhecimento.severity,
                            )
                          }
                        >
                          {
                            tituloSeveridade(
                              conhecimento.severity,
                            )
                          }
                        </span>
                      </div>

                      <p>
                        {
                          conhecimento.message
                        }
                      </p>

                      <div className="brain-knowledge-footer">
                        <span>
                          Evento:{" "}

                          <strong>
                            {
                              conhecimento.event_name
                            }
                          </strong>
                        </span>

                        <span>
                          {formatarData(
                            conhecimento.created_at,
                          )}
                        </span>

                        <button
                          type="button"
                          className={
                            conhecimento.resolved
                              ? "brain-reopen-button"
                              : "brain-resolve-button"
                          }
                          onClick={() =>
                            void alternarResolvido(
                              conhecimento,
                            )
                          }
                        >
                          {
                            conhecimento.resolved
                              ? "Reabrir"
                              : "Marcar como resolvido"
                          }
                        </button>
                      </div>
                    </div>
                  </article>
                ),
              )}
            </div>
          )}
        </section>
      </section>
    </div>
  );
}
