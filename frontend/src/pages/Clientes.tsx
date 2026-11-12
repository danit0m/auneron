import {
  AlertTriangle,
  Plus,
  RefreshCw,
  Search,
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
import ClienteModal from "../components/clientes/ClienteModal";
import ClienteTable from "../components/clientes/ClienteTable";
import SaudeCarteira from "../components/clientes/SaudeCarteira";
import ConfirmDeleteModal from "../components/clientes/ConfirmDeleteModal";
import { Header } from "../components/layout/Header";
import {
  filtrarContas,
  pluralizarClientes,
  resumirCadastro,
  type FiltroCarteira,
} from "../lib/carteira";
import type {
  Account,
  AccountCreate,
} from "../types/account";

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
  const [filtroCarteira, setFiltroCarteira] =
    useState<FiltroCarteira>(null);

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

  // Referência de "hoje" renovada a cada nova lista carregada; a
  // mesma data alimenta cards, filtros e a coluna Status da tabela.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const hoje = useMemo(() => new Date(), [clientes]);

  const clientesFiltrados = useMemo(() => {
    const pesquisaNormalizada = pesquisa
      .trim()
      .toLowerCase();

    return filtrarContas(
      clientes,
      hoje,
      filtroCarteira,
    ).filter((cliente) => {
      const nome = cliente.cliente.toLowerCase();
      const email = (cliente.email || "").toLowerCase();
      const whatsapp = (
        cliente.whatsapp || ""
      ).toLowerCase();

      return (
        !pesquisaNormalizada ||
        nome.includes(pesquisaNormalizada) ||
        email.includes(pesquisaNormalizada) ||
        whatsapp.includes(pesquisaNormalizada)
      );
    });
  }, [clientes, hoje, pesquisa, filtroCarteira]);

  const cadastro = useMemo(
    () => resumirCadastro(clientes),
    [clientes],
  );

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
        title="Clientes"
      />

      <section className="page-content clientes-page">
        <div className="clientes-page-heading">
          <p>
            {pluralizarClientes(cadastro.clientes)} ·{" "}
            {formatarMoeda(cadastro.valorCadastrado)} cadastrados
          </p>

          <button
            type="button"
            className="primary-button"
            onClick={abrirNovoCliente}
          >
            <Plus size={18} />
            Novo cliente
          </button>
        </div>

        <SaudeCarteira
          contas={clientes}
          filtro={filtroCarteira}
          onFiltrar={setFiltroCarteira}
        />

        <section id="clientes-lista" className="panel clientes-panel">
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
              títulos
            </span>

            {(pesquisa || filtroCarteira) && (
              <button
                type="button"
                className="clear-filter-button"
                onClick={() => {
                  setPesquisa("");
                  setFiltroCarteira(null);
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
              hoje={hoje}
              onEdit={editarCliente}
              onDelete={abrirConfirmacaoExclusao}
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
    </div>
  );
}
