import {
  AlertTriangle,
  Check,
  Info,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import api, {
  getApiErrorMessage,
} from "../../api/api";
import { useAuth } from "../../hooks/useAuth";
import type {
  ApprovalDetailsResponse,
  ApprovalRequestResponse,
} from "../../types/approval";
import type {
  HumanEscalationMaterializationResponse,
  MarkOverdueExecutionResponse,
  MarkOverdueMaterializationResponse,
  MarkOverdueWorkItemResponse,
  NbaDecisionEvidenceResponse,
  WorkItemListResponse,
} from "../../types/nba";
import { getActionLabel } from "../../types/nba";

interface RecommendationDecisionCardProps {
  accountId: number;
  dueDate: string;
  nba: NbaDecisionEvidenceResponse;
  podeMaterializarEscalonamento: boolean;
}

const DECISION_TYPE_LABELS: Record<
  NbaDecisionEvidenceResponse["decision"]["decision_type"],
  string
> = {
  single_action: "Uma ação recomendada",
  action_bundle: "Múltiplas ações recomendadas",
  no_action: "Nenhuma ação recomendada",
};

type MarkOverdueEpisodeState =
  | "not_materialized"
  | "pending_approval"
  | "approved_ready"
  | "rejected"
  | "expired"
  | "cancelled"
  | "completed";

function derivarEstadoMarkOverdue(
  workItem: MarkOverdueWorkItemResponse | null,
  approval: ApprovalRequestResponse | null,
): MarkOverdueEpisodeState | null {
  if (workItem === null) {
    return "not_materialized";
  }

  if (workItem.status === "completed") {
    return "completed";
  }

  if (approval === null) {
    return null;
  }

  if (approval.status === "pending") {
    return "pending_approval";
  }

  if (approval.status === "approved") {
    return "approved_ready";
  }

  return approval.status;
}

export default function RecommendationDecisionCard({
  accountId,
  dueDate,
  nba,
  podeMaterializarEscalonamento,
}: RecommendationDecisionCardProps) {
  const { hasPermission, hasAllPermissions } =
    useAuth();

  const [materializando, setMaterializando] =
    useState(false);
  const [resultado, setResultado] =
    useState<HumanEscalationMaterializationResponse | null>(
      null,
    );
  const [erroMaterializacao, setErroMaterializacao] =
    useState("");

  const acoesSelecionadas =
    nba.decision.selected_actions;
  const escalacaoSelecionada =
    acoesSelecionadas.includes(
      "escalate_to_human",
    );
  const markOverdueSelecionado =
    acoesSelecionadas.includes(
      "account.mark_overdue",
    );

  const workKeyEsperada =
    `account_mark_overdue:v1:${accountId}:${dueDate}`;

  const [
    carregandoEstadoMarkOverdue,
    setCarregandoEstadoMarkOverdue,
  ] = useState(false);
  const [
    erroEstadoMarkOverdue,
    setErroEstadoMarkOverdue,
  ] = useState("");
  const [
    markOverdueWorkItem,
    setMarkOverdueWorkItem,
  ] = useState<MarkOverdueWorkItemResponse | null>(
    null,
  );
  const [
    markOverdueApproval,
    setMarkOverdueApproval,
  ] = useState<ApprovalRequestResponse | null>(
    null,
  );

  const [
    materializandoMarkOverdue,
    setMaterializandoMarkOverdue,
  ] = useState(false);
  const [
    erroMaterializacaoMarkOverdue,
    setErroMaterializacaoMarkOverdue,
  ] = useState("");

  const [
    executandoMarkOverdue,
    setExecutandoMarkOverdue,
  ] = useState(false);
  const [
    erroExecucaoMarkOverdue,
    setErroExecucaoMarkOverdue,
  ] = useState("");
  const [
    resultadoExecucaoMarkOverdue,
    setResultadoExecucaoMarkOverdue,
  ] = useState<MarkOverdueExecutionResponse | null>(
    null,
  );

  // Contador de geração -- cada chamada captura o próprio requestId
  // e só aplica o resultado se nenhuma chamada mais nova tiver
  // começado nesse meio-tempo. Evita que uma resposta atrasada de
  // uma busca antiga (ex.: accountId/dueDate mudou, ou o refetch
  // pós-execute correu junto com um refetch de montagem) sobrescreva
  // o estado de um episódio mais novo.
  const estadoRequestIdRef = useRef(0);

  const buscarEstadoMarkOverdue =
    useCallback(async () => {
      const requestId =
        ++estadoRequestIdRef.current;

      setCarregandoEstadoMarkOverdue(true);
      setErroEstadoMarkOverdue("");

      try {
        const workResponse =
          await api.get<WorkItemListResponse>(
            "/work-items",
            {
              params: {
                scope_type: "account",
                account_id: accountId,
                limit: 100,
              },
            },
          );

        if (
          requestId !==
          estadoRequestIdRef.current
        ) {
          return;
        }

        const workItem =
          workResponse.data.items.find(
            (item) =>
              item.work_key ===
              workKeyEsperada,
          ) ?? null;

        setMarkOverdueWorkItem(workItem);

        const approvalRequestId =
          workItem?.context_data
            .approval_request_id;

        if (
          typeof approvalRequestId !==
          "number"
        ) {
          setMarkOverdueApproval(null);
          return;
        }

        const approvalResponse =
          await api.get<ApprovalDetailsResponse>(
            `/approvals/${approvalRequestId}`,
          );

        if (
          requestId !==
          estadoRequestIdRef.current
        ) {
          return;
        }

        setMarkOverdueApproval(
          approvalResponse.data.request,
        );
      } catch (error) {
        if (
          requestId !==
          estadoRequestIdRef.current
        ) {
          return;
        }

        console.error(
          "Erro ao buscar estado de account.mark_overdue:",
          error,
        );

        setErroEstadoMarkOverdue(
          getApiErrorMessage(
            error,
            "Não foi possível carregar o estado do episódio.",
          ),
        );
      } finally {
        if (
          requestId ===
          estadoRequestIdRef.current
        ) {
          setCarregandoEstadoMarkOverdue(
            false,
          );
        }
      }
    }, [accountId, workKeyEsperada]);

  useEffect(() => {
    if (!markOverdueSelecionado) {
      return;
    }

    const timeoutId = window.setTimeout(
      () => {
        void buscarEstadoMarkOverdue();
      },
      0,
    );

    return () =>
      window.clearTimeout(timeoutId);
  }, [
    markOverdueSelecionado,
    buscarEstadoMarkOverdue,
  ]);

  async function solicitarEscalonamento() {
    setMaterializando(true);
    setErroMaterializacao("");

    try {
      const response =
        await api.post<HumanEscalationMaterializationResponse>(
          "/recommendations/human-escalation/" +
            `accounts/${accountId}/episodes/${dueDate}/materialize`,
        );

      setResultado(response.data);
    } catch (error) {
      console.error(
        "Erro ao materializar escalonamento:",
        error,
      );

      setErroMaterializacao(
        getApiErrorMessage(
          error,
          "Não foi possível solicitar o escalonamento.",
        ),
      );
    } finally {
      setMaterializando(false);
    }
  }

  async function materializarMarkOverdue() {
    setMaterializandoMarkOverdue(true);
    setErroMaterializacaoMarkOverdue("");

    try {
      const response =
        await api.post<MarkOverdueMaterializationResponse>(
          "/recommendations/mark-overdue/" +
            `accounts/${accountId}/episodes/${dueDate}/materialize`,
        );

      setMarkOverdueWorkItem(
        response.data.work_item,
      );
      setMarkOverdueApproval(
        response.data.approval_request,
      );
    } catch (error) {
      console.error(
        "Erro ao materializar account.mark_overdue:",
        error,
      );

      setErroMaterializacaoMarkOverdue(
        getApiErrorMessage(
          error,
          "Não foi possível materializar o episódio.",
        ),
      );
    } finally {
      setMaterializandoMarkOverdue(false);
    }
  }

  async function executarMarkOverdue() {
    setExecutandoMarkOverdue(true);
    setErroExecucaoMarkOverdue("");

    try {
      const response =
        await api.post<MarkOverdueExecutionResponse>(
          "/recommendations/mark-overdue/" +
            `accounts/${accountId}/episodes/${dueDate}/execute`,
        );

      setResultadoExecucaoMarkOverdue(
        response.data,
      );

      // Fotografia pós-execução vem de GET, nunca de um novo
      // materialize -- WorkItem terminal faria a revalidação de
      // eligibility falhar fechada (409 status_not_open).
      await buscarEstadoMarkOverdue();
    } catch (error) {
      console.error(
        "Erro ao executar account.mark_overdue:",
        error,
      );

      setErroExecucaoMarkOverdue(
        getApiErrorMessage(
          error,
          "Não foi possível executar a ação.",
        ),
      );
    } finally {
      setExecutandoMarkOverdue(false);
    }
  }

  const estadoMarkOverdue =
    derivarEstadoMarkOverdue(
      markOverdueWorkItem,
      markOverdueApproval,
    );
  const podeMaterializarMarkOverdue =
    hasPermission("work:create");
  const podeExecutarMarkOverdue =
    hasAllPermissions([
      "skill:execute",
      "skill:execute_mutating",
      "clients.manage",
    ]);

  return (
    <div className="recommendation-card">
      <div className="recommendation-card-section">
        <h4>Decisão NBA</h4>
        <p className="recommendation-decision-type">
          {
            DECISION_TYPE_LABELS[
              nba.decision.decision_type
            ]
          }
        </p>

        {acoesSelecionadas.length === 0 ? (
          <p className="recommendation-no-action">
            Nenhuma ação governada é recomendável
            para este episódio no momento.
          </p>
        ) : (
          <ul className="recommendation-action-list">
            {acoesSelecionadas.map((actionKey) => (
              <li key={actionKey}>
                <Check size={16} />
                <span>
                  {getActionLabel(actionKey)}
                </span>

                {actionKey ===
                  "escalate_to_human" &&
                  (podeMaterializarEscalonamento ? (
                    <button
                      type="button"
                      className="recommendation-escalate-button"
                      disabled={
                        materializando ||
                        resultado !== null
                      }
                      onClick={() =>
                        void solicitarEscalonamento()
                      }
                    >
                      {materializando
                        ? "Solicitando..."
                        : "Solicitar escalonamento"}
                    </button>
                  ) : (
                    <span className="recommendation-no-authority">
                      Sem autoridade para
                      materializar
                    </span>
                  ))}

                {actionKey ===
                  "account.mark_overdue" && (
                  <div className="recommendation-mark-overdue-panel">
                    {carregandoEstadoMarkOverdue && (
                      <span className="recommendation-mark-overdue-status">
                        Carregando estado do
                        episódio...
                      </span>
                    )}

                    {/* Um erro de rede/HTTP nunca deve ser
                       interpretado como "not_materialized" --
                       o bloco de estado só renderiza quando a
                       busca terminou sem erro. O erro em si já
                       aparece via erroEstadoMarkOverdue mais
                       abaixo. */}
                    {!carregandoEstadoMarkOverdue &&
                      !erroEstadoMarkOverdue && (
                        <>
                          {estadoMarkOverdue ===
                            "not_materialized" &&
                            (podeMaterializarMarkOverdue ? (
                              <button
                                type="button"
                                className="recommendation-escalate-button"
                                disabled={
                                  materializandoMarkOverdue
                                }
                                onClick={() =>
                                  void materializarMarkOverdue()
                                }
                              >
                                {materializandoMarkOverdue
                                  ? "Materializando..."
                                  : "Materializar"}
                              </button>
                            ) : (
                              <span className="recommendation-no-authority">
                                Sem autoridade
                                para materializar
                              </span>
                            ))}

                          {estadoMarkOverdue ===
                            "pending_approval" && (
                            <span className="recommendation-mark-overdue-status">
                              Aguardando
                              aprovação humana.
                            </span>
                          )}

                          {estadoMarkOverdue ===
                            "approved_ready" &&
                            (podeExecutarMarkOverdue ? (
                              <button
                                type="button"
                                className="recommendation-escalate-button"
                                disabled={
                                  executandoMarkOverdue
                                }
                                onClick={() =>
                                  void executarMarkOverdue()
                                }
                              >
                                {executandoMarkOverdue
                                  ? "Executando..."
                                  : "Executar"}
                              </button>
                            ) : (
                              <span className="recommendation-no-authority">
                                Aprovado -- sem
                                autoridade para
                                executar
                              </span>
                            ))}

                          {(estadoMarkOverdue ===
                            "rejected" ||
                            estadoMarkOverdue ===
                              "expired" ||
                            estadoMarkOverdue ===
                              "cancelled") && (
                            <span className="recommendation-mark-overdue-status">
                              Aprovação{" "}
                              {estadoMarkOverdue}.
                            </span>
                          )}

                          {estadoMarkOverdue ===
                            "completed" && (
                            <span className="recommendation-mark-overdue-status">
                              Executado.
                            </span>
                          )}
                        </>
                      )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {escalacaoSelecionada && erroMaterializacao && (
        <div className="error-message recommendation-materialize-error">
          <AlertTriangle size={18} />
          <span>{erroMaterializacao}</span>
        </div>
      )}

      {resultado && (
        <div className="recommendation-materialize-result">
          <Check size={18} />
          <span>
            {resultado.created
              ? "Escalonamento criado."
              : "Escalonamento já existente."}
            {" "}
            Status atual do trabalho:{" "}
            {resultado.work_item.status}.
          </span>
        </div>
      )}

      {markOverdueSelecionado &&
        erroEstadoMarkOverdue && (
          <div className="error-message recommendation-materialize-error">
            <AlertTriangle size={18} />
            <span>{erroEstadoMarkOverdue}</span>
          </div>
        )}

      {erroMaterializacaoMarkOverdue && (
        <div className="error-message recommendation-materialize-error">
          <AlertTriangle size={18} />
          <span>
            {erroMaterializacaoMarkOverdue}
          </span>
        </div>
      )}

      {erroExecucaoMarkOverdue && (
        <div className="error-message recommendation-materialize-error">
          <AlertTriangle size={18} />
          <span>{erroExecucaoMarkOverdue}</span>
        </div>
      )}

      {resultadoExecucaoMarkOverdue && (
        <div className="recommendation-materialize-result">
          <Check size={18} />
          <span>
            {resultadoExecucaoMarkOverdue.duplicate
              ? "Execução já registrada."
              : "Execução concluída."}
            {" "}
            Conta:{" "}
            {
              resultadoExecucaoMarkOverdue
                .output.previous_status
            }{" "}
            →{" "}
            {
              resultadoExecucaoMarkOverdue
                .output.new_status
            }
            .
          </span>
        </div>
      )}

      <div className="recommendation-card-section recommendation-evidence">
        <h4>
          <Info size={15} />
          Por que esta recomendação?
        </h4>

        <ul className="recommendation-evidence-list">
          {nba.action_space.actions.map((action) => (
            <li key={action.action_key}>
              <span className="recommendation-evidence-action">
                {getActionLabel(action.action_key)}
              </span>
              <span className="recommendation-evidence-reason">
                {action.system_recommendable
                  ? "Recomendável."
                  : action.reason ??
                    "Sem motivo informado pelo servidor."}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
