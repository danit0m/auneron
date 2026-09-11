import {
  AlertTriangle,
  Check,
  ClipboardCheck,
  RefreshCw,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useState,
} from "react";

import api, {
  getApiErrorMessage,
} from "../api/api";
import { Header } from "../components/layout/Header";
import { useAuth } from "../hooks/useAuth";
import {
  getApprovalActionLabel,
} from "../types/approval";
import type {
  ApprovalDecisionResultResponse,
  ApprovalListResponse,
  ApprovalRequestResponse,
  ApprovalStatus,
} from "../types/approval";

import "./Approvals.css";

const FILTROS_STATUS: {
  value: ApprovalStatus | "todos";
  label: string;
}[] = [
  { value: "pending", label: "Pendentes" },
  { value: "approved", label: "Aprovadas" },
  { value: "rejected", label: "Rejeitadas" },
  { value: "expired", label: "Expiradas" },
  { value: "cancelled", label: "Canceladas" },
  { value: "todos", label: "Todos os status" },
];

function classeStatus(
  status: ApprovalStatus,
): string {
  if (status === "approved") {
    return "status-badge status-paid";
  }

  if (
    status === "rejected" ||
    status === "expired" ||
    status === "cancelled"
  ) {
    return "status-badge status-late";
  }

  return "status-badge status-open";
}

function rotuloStatus(
  status: ApprovalStatus,
): string {
  return (
    {
      pending: "Pendente",
      approved: "Aprovada",
      rejected: "Rejeitada",
      expired: "Expirada",
      cancelled: "Cancelada",
    } satisfies Record<ApprovalStatus, string>
  )[status];
}

function formatarDataHora(
  valor: string,
): string {
  return new Date(valor).toLocaleString(
    "pt-BR",
  );
}

export default function Approvals() {
  const { hasPermission } = useAuth();
  const podeDecidir = hasPermission(
    "approval:decide",
  );

  const [itens, setItens] = useState<
    ApprovalRequestResponse[]
  >([]);
  const [carregando, setCarregando] =
    useState(true);
  const [atualizando, setAtualizando] =
    useState(false);
  const [erro, setErro] = useState("");

  const [statusFiltro, setStatusFiltro] =
    useState<ApprovalStatus | "todos">(
      "pending",
    );

  const [
    decidindoId,
    setDecidindoId,
  ] = useState<number | null>(null);
  const [erroDecisao, setErroDecisao] =
    useState("");

  const carregarAprovacoes = useCallback(
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
          await api.get<ApprovalListResponse>(
            "/approvals",
            {
              params:
                statusFiltro === "todos"
                  ? { limit: 100 }
                  : {
                      status: [
                        statusFiltro,
                      ],
                      limit: 100,
                    },
            },
          );

        setItens(response.data.items);
      } catch (error) {
        console.error(
          "Erro ao carregar aprovações:",
          error,
        );

        setErro(
          getApiErrorMessage(
            error,
            "Não foi possível carregar as aprovações.",
          ),
        );
      } finally {
        setCarregando(false);
        setAtualizando(false);
      }
    },
    [statusFiltro],
  );

  useEffect(() => {
    const timeoutId = window.setTimeout(
      () => {
        void carregarAprovacoes();
      },
      0,
    );

    return () =>
      window.clearTimeout(timeoutId);
  }, [carregarAprovacoes]);

  async function decidir(
    requestId: number,
    decision: "approved" | "rejected",
  ) {
    setDecidindoId(requestId);
    setErroDecisao("");

    try {
      await api.post<
        ApprovalDecisionResultResponse
      >(
        `/approvals/${requestId}/decision`,
        { decision },
      );

      await carregarAprovacoes(false);
    } catch (error) {
      console.error(
        "Erro ao decidir aprovação:",
        error,
      );

      setErroDecisao(
        getApiErrorMessage(
          error,
          "Não foi possível registrar a decisão.",
        ),
      );
    } finally {
      setDecidindoId(null);
    }
  }

  if (carregando) {
    return (
      <div className="page">
        <Header
          title="Aprovações"
          subtitle="Ações governadas aguardando decisão humana"
        />

        <div className="state-container">
          <div className="loading-spinner" />
          <p>Carregando aprovações...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <Header
        title="Aprovações"
        subtitle="Ações governadas aguardando decisão humana"
      />

      <section className="page-content approvals-page">
        {erro && (
          <div className="error-message">
            <AlertTriangle size={22} />
            <span>{erro}</span>
          </div>
        )}

        <div className="panel approvals-panel">
          <div className="approvals-toolbar">
            <select
              value={statusFiltro}
              onChange={(event) =>
                setStatusFiltro(
                  event.target
                    .value as
                    | ApprovalStatus
                    | "todos",
                )
              }
            >
              {FILTROS_STATUS.map(
                (filtro) => (
                  <option
                    key={filtro.value}
                    value={filtro.value}
                  >
                    {filtro.label}
                  </option>
                ),
              )}
            </select>

            <button
              type="button"
              className="secondary-button"
              disabled={atualizando}
              onClick={() =>
                void carregarAprovacoes(
                  false,
                )
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

          {erroDecisao && (
            <div className="error-message">
              <AlertTriangle size={22} />
              <span>{erroDecisao}</span>
            </div>
          )}

          {itens.length === 0 ? (
            <div className="approvals-empty-state">
              <ClipboardCheck size={38} />
              <p>
                Nenhuma aprovação encontrada
                para este filtro.
              </p>
            </div>
          ) : (
            <div className="approvals-table-wrapper">
              <table className="approvals-table">
                <thead>
                  <tr>
                    <th>Ação</th>
                    <th>Status</th>
                    <th>Conta</th>
                    <th>Expira em</th>
                    <th>Criada em</th>
                    {podeDecidir && (
                      <th>Ações</th>
                    )}
                  </tr>
                </thead>

                <tbody>
                  {itens.map((item) => (
                    <tr key={item.request_id}>
                      <td>
                        {getApprovalActionLabel(
                          item.skill_key,
                        )}
                      </td>

                      <td>
                        <span
                          className={classeStatus(
                            item.status,
                          )}
                        >
                          {rotuloStatus(
                            item.status,
                          )}
                        </span>
                      </td>

                      <td>
                        {item.target_account_id ??
                          "—"}
                      </td>

                      <td>
                        {formatarDataHora(
                          item.expires_at,
                        )}
                      </td>

                      <td>
                        {formatarDataHora(
                          item.created_at,
                        )}
                      </td>

                      {podeDecidir && (
                        <td>
                          {item.status ===
                          "pending" ? (
                            <div className="approvals-actions">
                              <button
                                type="button"
                                className="approvals-approve-button"
                                disabled={
                                  decidindoId ===
                                  item.request_id
                                }
                                onClick={() =>
                                  void decidir(
                                    item.request_id,
                                    "approved",
                                  )
                                }
                              >
                                <Check
                                  size={16}
                                />
                                Aprovar
                              </button>

                              <button
                                type="button"
                                className="approvals-reject-button"
                                disabled={
                                  decidindoId ===
                                  item.request_id
                                }
                                onClick={() =>
                                  void decidir(
                                    item.request_id,
                                    "rejected",
                                  )
                                }
                              >
                                <X
                                  size={16}
                                />
                                Rejeitar
                              </button>
                            </div>
                          ) : (
                            <span className="approvals-decided-label">
                              —
                            </span>
                          )}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
