import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Plus,
  RefreshCw,
  Search,
  Users,
  WalletCards,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import api, {
  getApiErrorMessage,
} from "../api/api";
import ClienteClassificacaoModal from "../components/clientes/ClienteClassificacaoModal";
import ClienteModal from "../components/clientes/ClienteModal";
import ClienteTable from "../components/clientes/ClienteTable";
import ConfirmDeleteModal from "../components/clientes/ConfirmDeleteModal";
import { Header } from "../components/layout/Header";
import {
  groupAccountsForClassification,
  isCurrentClassificationCycle,
  pruneOrphanClassificationState,
} from "../lib/classification-grouping";
import type {
  Account,
  AccountCreate,
} from "../types/account";
import type {
  AccountClassificationResponse,
  ClassificationUIState,
} from "../types/classification";

function formatarMoeda(valor: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  }).format(valor);
}

export default function Clientes() {
  const [clientes, setClientes] = useState<Account[]>([]);

  const [carregando, setCarregando] = useState(true);
  const [atualizando, setAtualizando] = useState(false);
  const [erro, setErro] = useState("");

  const [pesquisa, setPesquisa] = useState("");
  const [statusFiltro, setStatusFiltro] = useState("todos");

  const [modalAberto, setModalAberto] = useState(false);
  const [clienteSelecionado, setClienteSelecionado] =
    useState<Account | null>(null);
  const [salvando, setSalvando] = useState(false);
  const [erroModal, setErroModal] = useState("");

  const [modalExclusaoAberto, setModalExclusaoAberto] =
    useState(false);
  const [clienteParaExcluir, setClienteParaExcluir] =
    useState<Account | null>(null);
  const [excluindo, setExcluindo] = useState(false);
  const [erroExclusao, setErroExclusao] = useState("");

  // Fatia 2C -- classificacao comportamental do cliente (read-only,
  // Fatia 2B). Indexado por account_id para cobrir toda linha da
  // tabela, inclusive contas com email=null.
  const [classificacoes, setClassificacoes] = useState<
    Record<number, ClassificationUIState>
  >({});

  const cicloClassificacaoRef = useRef(0);

  const [modalClassificacaoAberto, setModalClassificacaoAberto] =
    useState(false);
  const [classificacaoSelecionada, setClassificacaoSelecionada] =
    useState<AccountClassificationResponse | null>(null);

  const carregarClientes = useCallback(
    async (mostrarCarregamento = true) => {
      try {
        if (mostrarCarregamento) {
          setCarregando(true);
        } else {
          setAtualizando(true);
        }

        setErro("");

        const response = await api.get<Account[]>(
          "/accounts/",
          {
            params: {
              skip: 0,
              limit: 200,
            },
          },
        );

        setClientes(response.data);
      } catch (error) {
        console.error(
          "Erro ao carregar clientes:",
          error,
        );

        setErro(
          getApiErrorMessage(
            error,
            "Não foi possível carregar os clientes.",
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
        void carregarClientes();
      },
      0,
    );

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [carregarClientes]);

  // Fatia 2C -- busca a classificacao de cada cliente, deduplicada por
  // e-mail (1 request por e-mail unico; contas com email=null nunca
  // sao agrupadas entre si). Read-only: nao recalcula, nao cria, nao
  // altera nada -- so le o que a Fatia 2B ja expoe.
  useEffect(() => {
    const ciclo = cicloClassificacaoRef.current + 1;
    cicloClassificacaoRef.current = ciclo;

    // Reset intencional e sincrono do estado exibido para "loading" ao
    // trocar a lista de clientes (padrao documentado pelo proprio React
    // -- "resetting state when a prop changes"), para nunca mostrar a
    // classificacao velha de um cliente enquanto o novo ciclo de busca
    // esta em voo. A regra react-hooks/set-state-in-effect e uma
    // recomendacao geral contra cascatas de render evitaveis, nao uma
    // proibicao categorica -- aqui o reset e o proprio requisito do
    // contrato UX (estado "loading" explicito), entao o disable e
    // deliberado e escopado so a esta linha.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setClassificacoes((estadoAtual) => {
      const semOrfaos = pruneOrphanClassificationState(
        estadoAtual,
        clientes.map((cliente) => cliente.id),
      );

      const carregando: Record<number, ClassificationUIState> = {
        ...semOrfaos,
      };

      for (const cliente of clientes) {
        carregando[cliente.id] = { kind: "loading" };
      }

      return carregando;
    });

    if (clientes.length === 0) {
      return;
    }

    const grupos = groupAccountsForClassification(clientes);

    void Promise.allSettled(
      grupos.map(async (grupo) => {
        try {
          const response = await api.get<AccountClassificationResponse>(
            `/accounts/${grupo.representativeAccountId}/classification`,
          );

          return {
            grupo,
            estado: {
              kind: "resolved" as const,
              data: response.data,
            },
          };
        } catch (error) {
          // 404, erro de rede ou 5xx -- tudo vira "Indisponível" no
          // contrato congelado. Nunca é reinterpretado como
          // not_classified_yet, que só existe quando o backend
          // devolve 200 explicitamente com esse status.
          console.error(
            "Erro ao carregar classificação do cliente:",
            error,
          );

          return {
            grupo,
            estado: { kind: "request_failed" as const },
          };
        }
      }),
    ).then((resultados) => {
      if (
        !isCurrentClassificationCycle(
          ciclo,
          cicloClassificacaoRef.current,
        )
      ) {
        // Uma busca mais nova ja foi disparada -- descarta esta
        // resposta obsoleta para nao sobrescrever o ciclo atual.
        return;
      }

      setClassificacoes((estadoAtual) => {
        const proximoEstado = { ...estadoAtual };

        for (const resultado of resultados) {
          if (resultado.status !== "fulfilled") {
            continue;
          }

          const { grupo, estado } = resultado.value;

          for (const accountId of grupo.accountIds) {
            proximoEstado[accountId] = estado;
          }
        }

        return proximoEstado;
      });
    });
  }, [clientes]);

  function abrirModalClassificacao(
    _accountId: number,
    estado: ClassificationUIState,
  ) {
    // Modal só abre para status="classified" -- inclui
    // INSUFFICIENT_DATA (é classificacao real e persistida), mas
    // nunca not_classified_yet/loading/request_failed, que nao tem
    // evidencia para detalhar.
    if (
      estado.kind !== "resolved" ||
      estado.data.status !== "classified"
    ) {
      return;
    }

    setClassificacaoSelecionada(estado.data);
    setModalClassificacaoAberto(true);
  }

  function fecharModalClassificacao() {
    setModalClassificacaoAberto(false);
    setClassificacaoSelecionada(null);
  }

  const clientesFiltrados = useMemo(() => {
    const pesquisaNormalizada = pesquisa
      .trim()
      .toLowerCase();

    return clientes.filter((cliente) => {
      const nome = cliente.cliente.toLowerCase();
      const email = (cliente.email || "").toLowerCase();
      const whatsapp = (
        cliente.whatsapp || ""
      ).toLowerCase();
      const status = cliente.status.toLowerCase();

      const correspondePesquisa =
        !pesquisaNormalizada ||
        nome.includes(pesquisaNormalizada) ||
        email.includes(pesquisaNormalizada) ||
        whatsapp.includes(pesquisaNormalizada);

      const correspondeStatus =
        statusFiltro === "todos" ||
        status === statusFiltro;

      return correspondePesquisa && correspondeStatus;
    });
  }, [clientes, pesquisa, statusFiltro]);

  const resumo = useMemo(() => {
    const pagos = clientes.filter(
      (cliente) =>
        cliente.status.toLowerCase() === "pago",
    ).length;

    const abertos = clientes.filter(
      (cliente) =>
        cliente.status.toLowerCase() === "aberto",
    ).length;

    const atrasados = clientes.filter(
      (cliente) =>
        cliente.status.toLowerCase() === "atrasado",
    ).length;

    const valorTotal = clientes.reduce(
      (total, cliente) =>
        total + cliente.valor,
      0,
    );

    return {
      total: clientes.length,
      pagos,
      abertos,
      atrasados,
      valorTotal,
    };
  }, [clientes]);

  function abrirNovoCliente() {
    setClienteSelecionado(null);
    setErroModal("");
    setModalAberto(true);
  }

  function fecharModal() {
    if (salvando) {
      return;
    }

    setModalAberto(false);
    setClienteSelecionado(null);
    setErroModal("");
  }

  function editarCliente(cliente: Account) {
    setClienteSelecionado(cliente);
    setErroModal("");
    setModalAberto(true);
  }

  async function salvarCliente(
    dados: AccountCreate,
  ) {
    try {
      setSalvando(true);
      setErroModal("");

      if (clienteSelecionado) {
        const response =
          await api.put<Account>(
            `/accounts/${clienteSelecionado.id}`,
            dados,
          );

        setClientes((clientesAtuais) =>
          clientesAtuais.map((cliente) =>
            cliente.id === response.data.id
              ? response.data
              : cliente,
          ),
        );
      } else {
        const response =
          await api.post<Account>(
            "/accounts/",
            dados,
          );

        setClientes((clientesAtuais) => [
          ...clientesAtuais,
          response.data,
        ]);
      }

      fecharModal();

      await carregarClientes(false);
    } catch (error) {
      console.error(
        "Erro ao salvar cliente:",
        error,
      );

      setErroModal(
        getApiErrorMessage(
          error,
          clienteSelecionado
            ? "Não foi possível atualizar o cliente."
            : "Não foi possível cadastrar o cliente.",
        ),
      );
    } finally {
      setSalvando(false);
    }
  }

  function abrirConfirmacaoExclusao(
    cliente: Account,
  ) {
    setClienteParaExcluir(cliente);
    setErroExclusao("");
    setModalExclusaoAberto(true);
  }

  function fecharConfirmacaoExclusao() {
    if (excluindo) {
      return;
    }

    setModalExclusaoAberto(false);
    setClienteParaExcluir(null);
    setErroExclusao("");
  }

  async function confirmarExclusao() {
    if (!clienteParaExcluir) {
      return;
    }

    try {
      setExcluindo(true);
      setErroExclusao("");

      await api.delete(
        `/accounts/${clienteParaExcluir.id}`,
      );

      setClientes((clientesAtuais) =>
        clientesAtuais.filter(
          (cliente) =>
            cliente.id !== clienteParaExcluir.id,
        ),
      );

      setModalExclusaoAberto(false);
      setClienteParaExcluir(null);

      await carregarClientes(false);
    } catch (error) {
      console.error(
        "Erro ao excluir cliente:",
        error,
      );

      setErroExclusao(
        getApiErrorMessage(
          error,
          "Não foi possível excluir o cliente.",
        ),
      );
    } finally {
      setExcluindo(false);
    }
  }

  return (
    <div className="page">
      <Header
        title="Gestão de Clientes"
        subtitle="Cadastre, edite e acompanhe sua carteira de clientes"
      />

      <section className="page-content clientes-page">
        <div className="clientes-page-heading">
          <div>
            <span className="clientes-page-eyebrow">
              Carteira de clientes
            </span>

            <h2>
              Visão geral dos clientes
            </h2>

            <p>
              Consulte contatos, valores,
              vencimentos e situação financeira.
            </p>
          </div>

          <button
            type="button"
            className="primary-button"
            onClick={abrirNovoCliente}
          >
            <Plus size={18} />
            Novo cliente
          </button>
        </div>

        <div className="clientes-summary-grid">
          <button
            type="button"
            className="clientes-summary-card clientes-summary-card--interactive"
            aria-pressed={statusFiltro === "todos"}
            onClick={() => setStatusFiltro("todos")}
          >
            <div className="clientes-summary-icon clientes-summary-blue">
              <Users size={21} />
            </div>

            <div>
              <span>Total de clientes</span>
              <strong>{resumo.total}</strong>
              <small>Cadastros ativos</small>
            </div>
          </button>

          <button
            type="button"
            className="clientes-summary-card clientes-summary-card--interactive"
            aria-pressed={statusFiltro === "pago"}
            onClick={() => setStatusFiltro("pago")}
          >
            <div className="clientes-summary-icon clientes-summary-green">
              <CheckCircle2 size={21} />
            </div>

            <div>
              <span>Pagos</span>
              <strong>{resumo.pagos}</strong>
              <small>Clientes em dia</small>
            </div>
          </button>

          <button
            type="button"
            className="clientes-summary-card clientes-summary-card--interactive"
            aria-pressed={statusFiltro === "aberto"}
            onClick={() => setStatusFiltro("aberto")}
          >
            <div className="clientes-summary-icon clientes-summary-orange">
              <Clock3 size={21} />
            </div>

            <div>
              <span>Em aberto</span>
              <strong>{resumo.abertos}</strong>
              <small>Aguardando pagamento</small>
            </div>
          </button>

          <button
            type="button"
            className="clientes-summary-card clientes-summary-card--interactive"
            aria-pressed={statusFiltro === "atrasado"}
            onClick={() => setStatusFiltro("atrasado")}
          >
            <div className="clientes-summary-icon clientes-summary-red">
              <AlertTriangle size={21} />
            </div>

            <div>
              <span>Atrasados</span>
              <strong>{resumo.atrasados}</strong>
              <small>Exigem atenção</small>
            </div>
          </button>

          <article className="clientes-summary-card">
            <div className="clientes-summary-icon clientes-summary-purple">
              <WalletCards size={21} />
            </div>

            <div>
              <span>Valor total</span>
              <strong>
                {formatarMoeda(resumo.valorTotal)}
              </strong>
              <small>Carteira financeira</small>
            </div>
          </article>
        </div>

        <section className="panel clientes-panel">
          <div className="clientes-toolbar">
            <div className="clientes-search">
              <Search size={18} />

              <input
                type="search"
                value={pesquisa}
                placeholder="Pesquisar por nome, e-mail ou WhatsApp..."
                aria-label="Pesquisar clientes"
                onChange={(event) =>
                  setPesquisa(event.target.value)
                }
              />
            </div>

            <div className="clientes-toolbar-actions">
              <select
                className="clientes-status-filter"
                value={statusFiltro}
                aria-label="Filtrar clientes por status"
                onChange={(event) =>
                  setStatusFiltro(
                    event.target.value,
                  )
                }
              >
                <option value="todos">
                  Todos os status
                </option>
                <option value="pago">
                  Pago
                </option>
                <option value="aberto">
                  Aberto
                </option>
                <option value="atrasado">
                  Atrasado
                </option>
              </select>

              <button
                type="button"
                className="secondary-button"
                disabled={atualizando}
                onClick={() =>
                  void carregarClientes(false)
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

          <div className="clientes-results-info">
            <span>
              Exibindo{" "}
              <strong>
                {clientesFiltrados.length}
              </strong>{" "}
              de{" "}
              <strong>
                {clientes.length}
              </strong>{" "}
              clientes
            </span>

            {(pesquisa ||
              statusFiltro !== "todos") && (
              <button
                type="button"
                className="clear-filter-button"
                onClick={() => {
                  setPesquisa("");
                  setStatusFiltro("todos");
                }}
              >
                Limpar filtros
              </button>
            )}
          </div>

          {carregando ? (
            <div className="state-container clientes-loading">
              <div className="loading-spinner" />
              <p>Carregando clientes...</p>
            </div>
          ) : erro ? (
            <div className="clientes-error-state">
              <AlertTriangle size={23} />

              <div>
                <strong>
                  Não foi possível carregar os clientes
                </strong>
                <span>{erro}</span>
              </div>

              <button
                type="button"
                className="secondary-button"
                onClick={() =>
                  void carregarClientes()
                }
              >
                Tentar novamente
              </button>
            </div>
          ) : (
            <ClienteTable
              clientes={clientesFiltrados}
              classificacoes={classificacoes}
              onEdit={editarCliente}
              onDelete={abrirConfirmacaoExclusao}
              onClassificacaoClick={abrirModalClassificacao}
            />
          )}
        </section>
      </section>

      {modalAberto && (
        <ClienteModal
          aberto
          cliente={clienteSelecionado}
          salvando={salvando}
          erro={erroModal}
          onClose={fecharModal}
          onSubmit={salvarCliente}
        />
      )}

      <ConfirmDeleteModal
        aberto={modalExclusaoAberto}
        cliente={clienteParaExcluir}
        excluindo={excluindo}
        erro={erroExclusao}
        onClose={fecharConfirmacaoExclusao}
        onConfirm={confirmarExclusao}
      />

      {modalClassificacaoAberto && classificacaoSelecionada && (
        <ClienteClassificacaoModal
          aberto
          classificacao={classificacaoSelecionada}
          onClose={fecharModalClassificacao}
        />
      )}
    </div>
  );
}
