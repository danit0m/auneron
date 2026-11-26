import {
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Lightbulb,
  RefreshCw,
  ShieldCheck,
  Target,
  TrendingUp,
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
import DecisionContextCard from "../components/executive/DecisionContextCard";
import ExplainabilityCard from "../components/executive/ExplainabilityCard";
import KindBadge from "../components/executive/KindBadge";
import { rotuloDecisao, rotuloEvento } from "../design/labels";
import { Header } from "../components/layout/Header";
import { AuneronBot } from "../components/ui";
import type {
  DecisionHistoryResponse,
  LatestDecisionResponse,
  OrchestratorHealth,
  StoredDecision,
} from "../types/orchestrator";

import "./ExecutiveCenter.css";
import "../components/executive/styles/ExecutiveDecisionCard.css";

interface Recommendation {
  title: string;
  description: string;
  priority: "Crítica" | "Alta" | "Moderada";
  action: string;
}

interface ConfidenceProfile {
  label: string;
  assessment: string;
  className: string;
}

function traduzirDecisao(
  nome: string,
): string {
  return rotuloDecisao(nome);
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
      timeStyle: "short",
    },
  ).format(dataConvertida);
}

function formatarStatus(
  status: string,
): string {
  const statusNormalizado =
    status.trim().toLowerCase();

  if (
    statusNormalizado === "atrasado"
  ) {
    return "Atrasado";
  }

  if (statusNormalizado === "pago") {
    return "Pago";
  }

  return "Em aberto";
}

function classeStatus(
  status: string,
): string {
  const statusNormalizado =
    status.trim().toLowerCase();

  if (
    statusNormalizado === "atrasado"
  ) {
    return "executive-business-status executive-business-status-late";
  }

  if (statusNormalizado === "pago") {
    return "executive-business-status executive-business-status-paid";
  }

  return "executive-business-status executive-business-status-open";
}

function obterPerfilConfianca(
  percentual: number,
): ConfidenceProfile {
  if (percentual >= 95) {
    return {
      label: "Muito alta",
      assessment:
        "A análise apresenta forte sustentação nos dados avaliados e alto nível de segurança para apoiar a decisão.",
      className:
        "executive-confidence-level executive-confidence-level-excellent",
    };
  }

  if (percentual >= 85) {
    return {
      label: "Alta",
      assessment:
        "A análise apresenta boa consistência e pode apoiar a tomada de decisão com segurança.",
      className:
        "executive-confidence-level executive-confidence-level-high",
    };
  }

  if (percentual >= 70) {
    return {
      label: "Moderada",
      assessment:
        "A análise é consistente, mas recomenda-se acompanhamento antes de uma ação mais sensível.",
      className:
        "executive-confidence-level executive-confidence-level-medium",
    };
  }

  return {
    label: "Baixa",
    assessment:
      "Os dados atuais não oferecem sustentação suficiente. Recomenda-se revisão antes da tomada de decisão.",
    className:
      "executive-confidence-level executive-confidence-level-low",
  };
}

function gerarRecomendacao(
  decision: StoredDecision,
): Recommendation {
  const status =
    decision.context.status
      .trim()
      .toLowerCase();

  const valor =
    decision.context.valor;

  const diasAtraso =
    decision.context.dias_atraso;

  if (
    status === "atrasado" &&
    valor >= 30000
  ) {
    return {
      title:
        "Priorizar contato financeiro",
      description:
        "O cliente possui valor relevante em aberto e atraso financeiro ativo. Essa combinação aumenta a exposição financeira e exige acompanhamento imediato.",
      priority: "Crítica",
      action:
        "Realizar contato em até 24 horas e registrar um plano de regularização.",
    };
  }

  if (
    status === "atrasado" ||
    diasAtraso > 0
  ) {
    return {
      title:
        "Iniciar acompanhamento preventivo",
      description:
        "Foi identificado atraso financeiro que pode evoluir para um risco maior caso não seja tratado.",
      priority: "Alta",
      action:
        "Entrar em contato com o cliente e revisar a previsão de pagamento.",
    };
  }

  return {
    title:
      "Manter acompanhamento regular",
    description:
      "O contexto atual não indica risco crítico, mas deve continuar sendo acompanhado para identificar mudanças relevantes.",
    priority: "Moderada",
    action:
      "Manter o cliente no fluxo normal de acompanhamento financeiro.",
  };
}

function classePrioridade(
  prioridade: Recommendation["priority"],
): string {
  if (prioridade === "Crítica") {
    return "executive-recommendation-priority executive-recommendation-critical";
  }

  if (prioridade === "Alta") {
    return "executive-recommendation-priority executive-recommendation-high";
  }

  return "executive-recommendation-priority executive-recommendation-medium";
}

export default function ExecutiveCenter() {
  const [
    latestDecision,
    setLatestDecision,
  ] = useState<LatestDecisionResponse | null>(
    null,
  );

  const [
    decisionHistory,
    setDecisionHistory,
  ] = useState<DecisionHistoryResponse | null>(
    null,
  );

  const [health, setHealth] =
    useState<OrchestratorHealth | null>(
      null,
    );

  const [carregando, setCarregando] =
    useState(true);

  const [atualizando, setAtualizando] =
    useState(false);

  const [erro, setErro] =
    useState("");

  const [
    detalhesAbertos,
    setDetalhesAbertos,
  ] = useState(false);

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
          latestDecisionResponse,
          historyResponse,
          healthResponse,
        ] = await Promise.all([
          api.get<LatestDecisionResponse>(
            "/orchestrator/decision/latest",
          ),

          api.get<DecisionHistoryResponse>(
            "/orchestrator/decisions",
            {
              params: {
                limit: 8,
              },
            },
          ),

          api.get<OrchestratorHealth>(
            "/orchestrator/health",
          ),
        ]);

        setLatestDecision(
          latestDecisionResponse.data,
        );

        setDecisionHistory(
          historyResponse.data,
        );

        setHealth(
          healthResponse.data,
        );
      } catch (error) {
        console.error(
          "Erro ao carregar Executive Center:",
          error,
        );

        setErro(
          getApiErrorMessage(
            error,
            "Não foi possível carregar as informações executivas.",
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

  const decisaoAtual =
    latestDecision?.available
      ? latestDecision.decision
      : null;

  const recomendacao =
    useMemo(() => {
      if (!decisaoAtual) {
        return null;
      }

      return gerarRecomendacao(
        decisaoAtual,
      );
    }, [decisaoAtual]);

  const perfilConfianca =
    useMemo(() => {
      if (!decisaoAtual) {
        return null;
      }

      return obterPerfilConfianca(
        decisaoAtual
          .confidence_percentage,
      );
    }, [decisaoAtual]);

  const resumoExecutivo =
    useMemo(() => {
      const records =
        decisionHistory?.records ?? [];

      if (records.length === 0) {
        return {
          total: 0,
          confiancaMedia: 0,
          criticas: 0,
          valorAnalisado: 0,
        };
      }

      const confiancaTotal =
        records.reduce(
          (total, decision) =>
            total +
            decision.confidence_percentage,
          0,
        );

      const criticas =
        records.filter(
          (decision) =>
            decision.context.status
              .trim()
              .toLowerCase() ===
              "atrasado" ||
            decision.context.valor >=
              30000,
        ).length;

      const valorAnalisado =
        records.reduce(
          (total, decision) =>
            total +
            decision.context.valor,
          0,
        );

      return {
        total: records.length,

        confiancaMedia:
          confiancaTotal /
          records.length,

        criticas,

        valorAnalisado,
      };
    }, [decisionHistory]);

  if (carregando) {
    return (
      <div className="page">
        <Header
          title="Visão geral"
          subtitle="Visão executiva das decisões e recomendações da IA"
        />

        <section className="page-content">
          <div className="state-container">
            <AuneronBot mood="pensando" size="lg" message="Consultando as análises registradas..." />

            <p>
              Carregando visão executiva...
            </p>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="page">
      <Header
        title="Visão geral"
        subtitle="Visão executiva das decisões e recomendações da IA"
      />

      <section className="page-content executive-center-page">
        <div className="executive-center-hero">
          <div>
            <span className="executive-center-eyebrow">
              Inteligência para decisões
            </span>

            <h2>
              O que aconteceu, por que
              aconteceu e o que fazer agora
            </h2>

            <p>
              Uma visão simplificada para
              apoiar decisões de negócio,
              sem expor a complexidade
              interna da plataforma.
            </p>
          </div>

          <div className="executive-center-hero-icon">
            <BrainCircuit size={34} />
          </div>
        </div>

        {erro && (
          <div className="executive-center-error">
            <AlertTriangle size={20} />

            <span>{erro}</span>
          </div>
        )}

        <div className="executive-center-toolbar">
          <div className="executive-center-status">
            <span
              className={
                health?.status ===
                "healthy"
                  ? "executive-center-status-dot executive-center-status-online"
                  : "executive-center-status-dot executive-center-status-offline"
              }
            />

            <div>
              <strong>
                Inteligência operacional
              </strong>

              <span>
                {health?.status ===
                "healthy"
                  ? "Disponível para análise"
                  : "Temporariamente indisponível"}
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
              : "Atualizar visão"}
          </button>
        </div>

        <div className="executive-center-summary">
          <article>
            <div className="executive-center-summary-icon executive-summary-blue">
              <Target size={20} />
            </div>

            <div>
              <span>
                Decisões recentes
              </span>

              <strong>
                {resumoExecutivo.total}
              </strong>

              <small>
                Histórico disponível
              </small>
            </div>
          </article>

          <article>
            <div className="executive-center-summary-icon executive-summary-green">
              <ShieldCheck size={20} />
            </div>

            <div>
              <span>
                Nível de confiança médio
              </span>

              <strong>
                {resumoExecutivo.confiancaMedia.toFixed(
                  1,
                )}
                %
              </strong>

              <small>
                Sustentação das análises
              </small>
            </div>
          </article>

          <article>
            <div className="executive-center-summary-icon executive-summary-orange">
              <AlertTriangle size={20} />
            </div>

            <div>
              <span>
                Casos prioritários
              </span>

              <strong>
                {resumoExecutivo.criticas}
              </strong>

              <small>
                Necessitam atenção
              </small>
            </div>
          </article>

          <article>
            <div className="executive-center-summary-icon executive-summary-purple">
              <TrendingUp size={20} />
            </div>

            <div>
              <span>
                Valor analisado
              </span>

              <strong>
                {formatarMoeda(
                  resumoExecutivo.valorAnalisado,
                )}
              </strong>

              <small>
                Nas decisões recentes
              </small>
            </div>
          </article>
        </div>

        <section className="executive-assistant" aria-labelledby="executive-assistant-title">
          <AuneronBot
            mood={atualizando ? "pensando" : erro ? "preocupado" : decisaoAtual ? "atento" : "dormindo"}
            size="lg"
            label="AuneronBot, assistente do Auneron"
          />
          <div className="executive-assistant-content">
            <span className="executive-center-eyebrow">Assistente Auneron</span>
            {decisaoAtual && !erro && !atualizando && <KindBadge kind="analise" />}
            <h2 id="executive-assistant-title">
              {atualizando ? "Atualizando as informações" : erro ? "Não consegui atualizar a visão executiva" : decisaoAtual ? traduzirDecisao(decisaoAtual.decision_name) : "Ainda não há uma análise disponível"}
            </h2>
            <div className="executive-assistant-message" role="status">
              {atualizando ? (
                <p>Estou consultando os resultados registrados. Aguarde a atualização.</p>
              ) : erro ? (
                <p>Ocorreu uma falha ao consultar os dados. Use “Atualizar visão” para tentar novamente.</p>
              ) : decisaoAtual ? (
                <>
                  <p>{decisaoAtual.reason}</p>
                  <span>Análise registrada em {formatarData(decisaoAtual.created_at)}. Consulte o contexto e as evidências abaixo.</span>
                </>
              ) : (
                <p>Não encontrei análises registradas para apresentar. Quando houver uma avaliação disponível, o resultado e seus motivos aparecerão aqui.</p>
              )}
            </div>
            {decisaoAtual && !erro && !atualizando && (
              <a
                className="executive-assistant-details"
                href="#executive-analysis-details"
                onClick={() => setDetalhesAbertos(true)}
              >
                Ver detalhes da análise
              </a>
            )}
          </div>
        </section>

        {decisaoAtual && (
          <>
            <article className="executive-business-decision-card">
              <div className="executive-business-decision-header">
                <div>
                  <span className="executive-business-eyebrow">
                    <KindBadge kind="analise" />
                  </span>

                  <h3>
                    {traduzirDecisao(
                      decisaoAtual.decision_name,
                    )}
                  </h3>
                </div>

                {perfilConfianca && (
                  <div
                    className={
                      perfilConfianca.className
                    }
                  >
                    <strong>
                      {decisaoAtual.confidence_percentage.toFixed(
                        1,
                      )}
                      %
                    </strong>

                    <span>
                      Nível de confiança:{" "}
                      {perfilConfianca.label}
                    </span>
                  </div>
                )}
              </div>

              <div className="executive-business-reason">
                <span>
                  Fundamentação da análise
                </span>

                <p>
                  {decisaoAtual.reason}
                </p>
              </div>

              <div className="executive-business-grid">
                <div>
                  <span>Cliente</span>

                  <strong>
                    {
                      decisaoAtual.context
                        .cliente
                    }
                  </strong>
                </div>

                <div>
                  <span>
                    Valor considerado
                  </span>

                  <strong>
                    {formatarMoeda(
                      decisaoAtual.context
                        .valor,
                    )}
                  </strong>
                </div>

                <div>
                  <span>
                    Situação financeira
                  </span>

                  <strong
                    className={classeStatus(
                      decisaoAtual.context
                        .status,
                    )}
                  >
                    {formatarStatus(
                      decisaoAtual.context
                        .status,
                    )}
                  </strong>
                </div>

                <div>
                  <span>
                    Análise concluída
                  </span>

                  <strong>
                    {formatarData(
                      decisaoAtual.created_at,
                    )}
                  </strong>
                </div>
              </div>

              {perfilConfianca && (
                <div className="executive-business-confidence">
                  <div className="executive-business-confidence-heading">
                    <span>
                      Nível de confiança
                    </span>

                    <strong>
                      {decisaoAtual.confidence_percentage.toFixed(1)}% · {perfilConfianca.label}
                    </strong>
                  </div>

                  <div className="executive-business-confidence-track">
                    <div
                      className="executive-business-confidence-value"
                      style={{
                        width: `${Math.min(
                          Math.max(
                            decisaoAtual
                              .confidence_percentage,
                            0,
                          ),
                          100,
                        )}%`,
                      }}
                    />
                  </div>

                  <p>
                    {
                      perfilConfianca.assessment
                    }
                  </p>
                </div>
              )}
            </article>

            <div className="executive-center-intelligence-grid">
              <ExplainabilityCard
                data={latestDecision}
              />

              <DecisionContextCard
                data={latestDecision}
              />
            </div>
          </>
        )}

        {recomendacao && (
          <article className="executive-recommendation-card">
            <div className="executive-recommendation-header">
              <div className="executive-recommendation-title">
                <div className="executive-recommendation-icon">
                  <Lightbulb size={21} />
                </div>

                <div>
                  <KindBadge kind="recomendacao" />

                  <h3>
                    {recomendacao.title}
                  </h3>
                </div>
              </div>

              <span
                className={classePrioridade(
                  recomendacao.priority,
                )}
              >
                Prioridade{" "}
                {recomendacao.priority}
              </span>
            </div>

            <p className="executive-recommendation-description">
              {recomendacao.description}
            </p>

            <p className="executive-recommendation-note">
              Esta é uma recomendação: nenhuma ação foi executada por ela.
            </p>

            <div className="executive-recommendation-action">
              <CheckCircle2 size={18} />

              <div>
                <span>
                  Próxima ação recomendada
                </span>

                <strong>
                  {recomendacao.action}
                </strong>
              </div>
            </div>
          </article>
        )}

        {decisaoAtual && (
          <article id="executive-analysis-details" className="executive-analysis-details">
            <button
              type="button"
              className="executive-analysis-details-toggle"
              aria-expanded={detalhesAbertos}
              onClick={() =>
                setDetalhesAbertos(
                  (estadoAtual) =>
                    !estadoAtual,
                )
              }
            >
              <div>
                <span>
                  Informações de auditoria
                </span>

                <strong>
                  Ver detalhes da análise
                </strong>
              </div>

              {detalhesAbertos ? (
                <ChevronUp size={18} />
              ) : (
                <ChevronDown size={18} />
              )}
            </button>

            {detalhesAbertos && (
              <div className="executive-analysis-details-content">
                <div>
                  <span>
                    Identificador da análise
                  </span>

                  <strong>
                    {
                      decisaoAtual.decision_id
                    }
                  </strong>
                </div>

                <div>
                  <span>
                    Regra interna aplicada
                  </span>

                  <strong>
                    {rotuloDecisao(decisaoAtual.decision_name)}
                  </strong>
                </div>

                <div>
                  <span>
                    Evento de origem
                  </span>

                  <strong>
                    {rotuloEvento(decisaoAtual.event_name)}
                  </strong>
                </div>
              </div>
            )}
          </article>
        )}

        <section className="executive-history-panel">
          <div className="executive-history-header">
            <div>
              <span>
                Histórico executivo
              </span>

              <h3>
                Decisões recentes
              </h3>

              <p>
                Uma visão resumida das
                avaliações mais recentes.
              </p>
            </div>
          </div>

          {(decisionHistory?.records
            .length ?? 0) === 0 ? (
            <div className="executive-history-empty">
              <BrainCircuit size={34} />

              <strong>
                Nenhuma decisão registrada
              </strong>

              <p>
                As próximas análises
                aparecerão neste histórico.
              </p>
            </div>
          ) : (
            <div className="executive-history-list">
              {decisionHistory?.records.map(
                (decision) => (
                  <article
                    key={
                      decision.decision_id
                    }
                    className="executive-history-item"
                  >
                    <div className="executive-history-main">
                      <div>
                        <strong>
                          {traduzirDecisao(
                            decision.decision_name,
                          )}
                        </strong>

                        <span>
                          {
                            decision.context
                              .cliente
                          }
                        </span>
                      </div>

                      <span className="executive-history-confidence">
                        {decision.confidence_percentage.toFixed(
                          1,
                        )}
                        %
                      </span>
                    </div>

                    <p>
                      {decision.reason}
                    </p>

                    <div className="executive-history-meta">
                      <span>
                        {formatarMoeda(
                          decision.context
                            .valor,
                        )}
                      </span>

                      <span>
                        {formatarStatus(
                          decision.context
                            .status,
                        )}
                      </span>

                      <span>
                        {
                          decision.context
                            .dias_atraso
                        }{" "}
                        {decision.context
                          .dias_atraso === 1
                          ? "dia em atraso"
                          : "dias em atraso"}
                      </span>

                      <span>
                        {formatarData(
                          decision.created_at,
                        )}
                      </span>
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
