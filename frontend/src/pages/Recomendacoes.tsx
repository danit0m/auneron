import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Lightbulb,
  RefreshCw,
} from "lucide-react";
import {
  Fragment,
  useCallback,
  useEffect,
  useState,
} from "react";

import api, {
  getApiErrorMessage,
} from "../api/api";
import { Header } from "../components/layout/Header";
import RecommendationDecisionCard from "../components/recommendations/RecommendationDecisionCard";
import { useAuth } from "../hooks/useAuth";
import type { Account } from "../types/account";
import type {
  NbaDecisionEvidenceResponse,
} from "../types/nba";

import "./Recomendacoes.css";

interface LinhaEstado {
  expandido: boolean;
  carregandoNba: boolean;
  nba: NbaDecisionEvidenceResponse | null;
  erroNba: string;
}

function formatarMoeda(valor: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  }).format(valor);
}

function formatarData(valor: string): string {
  return new Date(
    `${valor}T00:00:00`,
  ).toLocaleDateString("pt-BR");
}

/**
 * O campo bruto Account.status raramente chega a "atrasado" por si
 * só -- o lifecycle usado pela elegibilidade/NBA (evaluate_
 * receivable_lifecycle) considera vencida qualquer conta com
 * vencimento < hoje e status != "pago", independente do valor exato
 * do campo. O filtro aqui espelha esse mesmo critério, nunca inventa
 * um terceiro conceito de "atrasado".
 */
function estaVencida(conta: Account): boolean {
  const hojeIso = new Date()
    .toISOString()
    .slice(0, 10);

  return (
    conta.status !== "pago" &&
    conta.vencimento < hojeIso
  );
}

export default function Recomendacoes() {
  const { hasPermission } = useAuth();
  const podeMaterializarEscalonamento =
    hasPermission("work:create");

  const [contas, setContas] = useState<Account[]>(
    [],
  );
  const [carregando, setCarregando] =
    useState(true);
  const [atualizando, setAtualizando] =
    useState(false);
  const [erro, setErro] = useState("");

  const [linhas, setLinhas] = useState<
    Record<number, LinhaEstado>
  >({});

  const carregarContas = useCallback(
    async (mostrarCarregamento = true) => {
      try {
        if (mostrarCarregamento) {
          setCarregando(true);
        } else {
          setAtualizando(true);
        }

        setErro("");

        const response = await api.get<
          Account[]
        >("/accounts/", {
          params: {
            limit: 200,
          },
        });

        setContas(
          response.data.filter(
            estaVencida,
          ),
        );
      } catch (error) {
        console.error(
          "Erro ao carregar contas atrasadas:",
          error,
        );

        setErro(
          getApiErrorMessage(
            error,
            "Não foi possível carregar as contas atrasadas.",
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
    const timeoutId = window.setTimeout(() => {
      void carregarContas();
    }, 0);

    return () =>
      window.clearTimeout(timeoutId);
  }, [carregarContas]);

  async function alternarLinha(conta: Account) {
    const atual = linhas[conta.id];

    if (atual?.expandido) {
      // Fechar descarta a decisão carregada -- nunca reaproveitamos
      // NBA entre expansões, a próxima abertura é sempre uma nova
      // consulta ao servidor.
      setLinhas((anterior) => ({
        ...anterior,
        [conta.id]: {
          expandido: false,
          carregandoNba: false,
          nba: null,
          erroNba: "",
        },
      }));
      return;
    }

    setLinhas((anterior) => ({
      ...anterior,
      [conta.id]: {
        expandido: true,
        carregandoNba: true,
        nba: null,
        erroNba: "",
      },
    }));

    try {
      const response = await api.get<
        NbaDecisionEvidenceResponse
      >(
        "/recommendations/next-best-action/" +
          `accounts/${conta.id}/episodes/${conta.vencimento}`,
      );

      setLinhas((anterior) => ({
        ...anterior,
        [conta.id]: {
          expandido: true,
          carregandoNba: false,
          nba: response.data,
          erroNba: "",
        },
      }));
    } catch (error) {
      console.error(
        "Erro ao carregar recomendação NBA:",
        error,
      );

      setLinhas((anterior) => ({
        ...anterior,
        [conta.id]: {
          expandido: true,
          carregandoNba: false,
          nba: null,
          erroNba: getApiErrorMessage(
            error,
            "Não foi possível carregar a recomendação.",
          ),
        },
      }));
    }
  }

  if (carregando) {
    return (
      <div className="page">
        <Header
          title="Recomendações"
          subtitle="Decisões de próxima melhor ação para contas atrasadas"
        />

        <div className="state-container">
          <div className="loading-spinner" />
          <p>Carregando contas atrasadas...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <Header
        title="Recomendações"
        subtitle="Decisões de próxima melhor ação para contas atrasadas"
      />

      <section className="page-content recomendacoes-page">
        {erro && (
          <div className="error-message">
            <AlertTriangle size={22} />
            <span>{erro}</span>
          </div>
        )}

        <div className="panel recomendacoes-panel">
          <div className="recomendacoes-toolbar">
            <button
              type="button"
              className="secondary-button"
              disabled={atualizando}
              onClick={() =>
                void carregarContas(false)
              }
            >
              <RefreshCw
                size={16}
                className={
                  atualizando
                    ? "rotating-icon"
                    : ""
                }
              />
              Atualizar
            </button>
          </div>

          {contas.length === 0 ? (
            <div className="recomendacoes-empty-state">
              <Lightbulb size={38} />
              <p>
                Nenhuma conta atrasada no
                momento.
              </p>
            </div>
          ) : (
            <div className="recomendacoes-table-wrapper">
              <table className="recomendacoes-table">
                <thead>
                  <tr>
                    <th />
                    <th>Cliente</th>
                    <th>Valor</th>
                    <th>Vencimento</th>
                  </tr>
                </thead>

                <tbody>
                  {contas.map((conta) => {
                    const linha =
                      linhas[conta.id];

                    return (
                      <Fragment key={conta.id}>
                        <tr
                          className="recomendacoes-row"
                          onClick={() =>
                            void alternarLinha(
                              conta,
                            )
                          }
                        >
                          <td className="recomendacoes-expand-cell">
                            {linha?.expandido ? (
                              <ChevronDown
                                size={16}
                              />
                            ) : (
                              <ChevronRight
                                size={16}
                              />
                            )}
                          </td>
                          <td>{conta.cliente}</td>
                          <td>
                            {formatarMoeda(
                              conta.valor,
                            )}
                          </td>
                          <td>
                            {formatarData(
                              conta.vencimento,
                            )}
                          </td>
                        </tr>

                        {linha?.expandido && (
                          <tr className="recomendacoes-detail-row">
                            <td
                              colSpan={4}
                            >
                              {linha.carregandoNba ? (
                                <div className="recomendacoes-detail-loading">
                                  <div className="loading-spinner" />
                                  <span>
                                    Carregando
                                    recomendação...
                                  </span>
                                </div>
                              ) : linha.erroNba ? (
                                <div className="error-message">
                                  <AlertTriangle
                                    size={18}
                                  />
                                  <span>
                                    {
                                      linha.erroNba
                                    }
                                  </span>
                                </div>
                              ) : linha.nba ? (
                                <RecommendationDecisionCard
                                  accountId={
                                    conta.id
                                  }
                                  dueDate={
                                    conta.vencimento
                                  }
                                  nba={linha.nba}
                                  podeMaterializarEscalonamento={
                                    podeMaterializarEscalonamento
                                  }
                                />
                              ) : null}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
