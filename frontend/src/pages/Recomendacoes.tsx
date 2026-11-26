import {
  AlertTriangle,
  RefreshCw,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useState,
} from "react";

import api, {
  getApiErrorMessage,
} from "../api/api";
import KindBadge from "../components/executive/KindBadge";
import { Header } from "../components/layout/Header";
import { AuneronBot } from "../components/ui";
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
          <AuneronBot mood="pensando" size="lg" message="Estou consultando as contas para apresentar as recomendações disponíveis." />
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
        <section className="recommendation-chat-intro" aria-label="Assistente Auneron">
          <AuneronBot mood={atualizando ? "pensando" : erro ? "preocupado" : "atento"} size="lg" label="AuneronBot" />
          <div className="recommendation-chat-bubble" role="status">
            <strong>Assistente Auneron</strong>
            <KindBadge kind="recomendacao" />
            <p>
              {atualizando
                ? "Estou atualizando a consulta das contas."
                : erro
                  ? "Não consegui consultar as contas. Tente atualizar para continuar."
                  : contas.length === 0
                    ? "Não encontrei contas em atraso nesta consulta. Quando houver contas disponíveis, você poderá consultar minhas recomendações aqui."
                    : "Escolha uma conta abaixo. Vou apresentar a recomendação disponível, explicar os motivos e mostrar os próximos passos."}
            </p>
          </div>
        </section>

        {erro && <div className="error-message"><AlertTriangle size={22} /><span>{erro}</span></div>}

        <div className="recomendacoes-toolbar">
          <button type="button" className="secondary-button" disabled={atualizando} onClick={() => void carregarContas(false)}>
            <RefreshCw size={16} className={atualizando ? "rotating-icon" : ""} />
            Atualizar contas
          </button>
        </div>

        <div className="recommendation-conversations">
          {!erro && !atualizando && contas.map((conta) => {
            const linha = linhas[conta.id];
            return (
              <article className="recommendation-conversation" key={conta.id}>
                <div className="recommendation-account-context">
                  <div>
                    <span>Conta para acompanhamento</span>
                    <h2>{conta.cliente}</h2>
                    <p>{formatarMoeda(conta.valor)} · Vencimento em {formatarData(conta.vencimento)}</p>
                  </div>
                  <button
                    type="button"
                    className="secondary-button"
                    aria-expanded={linha?.expandido ?? false}
                    aria-controls={`recommendation-reply-${conta.id}`}
                    onClick={() => void alternarLinha(conta)}
                  >
                    {linha?.expandido ? "Recolher conversa" : "Consultar recomendação"}
                  </button>
                </div>
                <div id={`recommendation-reply-${conta.id}`} hidden={!linha?.expandido}>
                  {linha?.expandido && (
                    linha.carregandoNba ? (
                      <AuneronBot className="recommendation-chat-loading" mood="pensando" size="md" message="Estou consultando a recomendação para esta conta..." />
                    ) : linha.erroNba ? (
                      <div className="recommendation-chat-bubble" role="alert">
                        <strong>Assistente Auneron</strong>
                        <p>Não consegui obter a recomendação. {linha.erroNba}</p>
                        <p>Recolha a conversa e consulte novamente para tentar outra vez.</p>
                      </div>
                    ) : linha.nba ? (
                      <RecommendationDecisionCard
                        accountId={conta.id}
                        dueDate={conta.vencimento}
                        nba={linha.nba}
                        podeMaterializarEscalonamento={podeMaterializarEscalonamento}
                      />
                    ) : null
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
