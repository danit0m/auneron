import {
  AlertTriangle,
  Check,
  ClipboardList,
  FileText,
  Info,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
} from "react";

import api, {
  fetchEscalationObservations,
  getApiErrorMessage,
} from "../../api/api";
import { useAuth } from "../../hooks/useAuth";
import { useWorkItemByKey } from "../../hooks/useWorkItemByKey";
import type {
  ApprovalDetailsResponse,
  ApprovalRequestResponse,
} from "../../types/approval";
import type {
  EscalationObservationResponse,
} from "../../types/escalationObservation";
import type {
  MarkOverdueExecutionResponse,
  MarkOverdueMaterializationResponse,
  NbaDecisionEvidenceResponse,
  WorkItemSummaryResponse,
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

/**
 * Traducao das razoes de inelegibilidade ja definidas no backend
 * (IneligibilityReason, app/core/human_escalation_eligibility.py) --
 * vocabulario fechado, nunca texto livre. Codigo desconhecido cai no
 * fallback ja existente ("Sem motivo informado pelo servidor."),
 * nunca quebra se o backend adicionar um codigo novo.
 */
const INELIGIBILITY_REASON_LABELS: Record<string, string> = {
  due_date_mismatch:
    "Data de vencimento não corresponde ao episódio atual.",
  account_paid: "Esta conta já foi paga.",
  lifecycle_not_overdue:
    "Esta conta não está mais em atraso.",
  active_escalation_exists:
    "Este episódio já tem um escalonamento em andamento.",
};

function traduzirRazao(
  reason: string | null,
): string | null {
  if (reason === null) {
    return null;
  }

  return INELIGIBILITY_REASON_LABELS[reason] ?? reason;
}

/**
 * Vocabulario fechado de VALUE-2.2/2.3 (ASSESSMENT_CODES,
 * app/models/escalation_observation.py) -- so estes 5 valores existem,
 * sem opcao de texto livre.
 */
const ASSESSMENT_CODE_LABELS: Record<string, string> = {
  contact_made: "Contato realizado",
  payment_promised: "Pagamento prometido",
  payment_refused: "Pagamento recusado",
  unreachable: "Cliente inalcançável",
  partial_agreement: "Acordo parcial",
};

function formatarDataHora(valor: string): string {
  return new Date(valor).toLocaleString("pt-BR");
}

type MarkOverdueEpisodeState =
  | "not_materialized"
  | "pending_approval"
  | "approved_ready"
  | "rejected"
  | "expired"
  | "cancelled"
  | "completed";

function derivarEstadoMarkOverdue(
  workItem: WorkItemSummaryResponse | null,
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

  const workKeyMarkOverdue =
    `account_mark_overdue:v1:${accountId}:${dueDate}`;
  const workKeyEscalonamento =
    `human_escalation:v1:${accountId}:${dueDate}`;

  const {
    workItem: markOverdueWorkItem,
    loading: carregandoEstadoMarkOverdue,
    error: erroEstadoMarkOverdue,
    refetch: refetchMarkOverdueWorkItem,
  } = useWorkItemByKey(
    accountId,
    workKeyMarkOverdue,
    markOverdueSelecionado,
  );

  const [
    markOverdueApproval,
    setMarkOverdueApproval,
  ] = useState<ApprovalRequestResponse | null>(
    null,
  );

  // Busca a ApprovalRequest -- preocupacao especifica de
  // account.mark_overdue, fora do escopo do useWorkItemByKey (que so
  // resolve o WorkItem). Encadeada a partir do WorkItem que o hook
  // devolve.
  useEffect(() => {
    const approvalRequestId =
      markOverdueWorkItem?.context_data
        .approval_request_id;

    let cancelado = false;

    void (async () => {
      if (typeof approvalRequestId !== "number") {
        if (!cancelado) {
          setMarkOverdueApproval(null);
        }
        return;
      }

      try {
        const approvalResponse =
          await api.get<ApprovalDetailsResponse>(
            `/approvals/${approvalRequestId}`,
          );

        if (!cancelado) {
          setMarkOverdueApproval(
            approvalResponse.data.request,
          );
        }
      } catch (error) {
        if (!cancelado) {
          console.error(
            "Erro ao buscar aprovação de account.mark_overdue:",
            error,
          );
        }
      }
    })();

    return () => {
      cancelado = true;
    };
  }, [markOverdueWorkItem]);

  // Sempre habilitado (nao condicionado a escalacaoSelecionada): uma
  // vez que o escalonamento existe, a politica de elegibilidade (R3,
  // active_escalation_exists) para de recomendar escalate_to_human --
  // mas o painel persistente precisa continuar aparecendo mesmo
  // assim, senao a UI regride exatamente para a ruptura que o
  // VALUE-3.1 existe para corrigir.
  const {
    workItem: escalationWorkItem,
    loading: carregandoEscalationWorkItem,
    error: erroEscalationWorkItem,
    refetch: refetchEscalationWorkItem,
  } = useWorkItemByKey(
    accountId,
    workKeyEscalonamento,
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

  const [
    observations,
    setObservations,
  ] = useState<EscalationObservationResponse[]>([]);
  const [nextCursor, setNextCursor] =
    useState<number | null>(null);
  const [
    carregandoObservations,
    setCarregandoObservations,
  ] = useState(false);
  const [
    carregandoMaisObservations,
    setCarregandoMaisObservations,
  ] = useState(false);
  const [
    erroObservations,
    setErroObservations,
  ] = useState("");
  const observationsRequestIdRef = useRef(0);

  useEffect(() => {
    const requestId =
      ++observationsRequestIdRef.current;

    void (async () => {
      if (escalationWorkItem === null) {
        if (
          requestId ===
          observationsRequestIdRef.current
        ) {
          setObservations([]);
          setNextCursor(null);
          setErroObservations("");
        }
        return;
      }

      const workItemId = escalationWorkItem.id;

      setCarregandoObservations(true);
      setErroObservations("");

      try {
        const page =
          await fetchEscalationObservations(
            workItemId,
          );

        if (
          requestId !==
          observationsRequestIdRef.current
        ) {
          return;
        }

        setObservations(page.items);
        setNextCursor(page.next_cursor);
      } catch (error) {
        if (
          requestId !==
          observationsRequestIdRef.current
        ) {
          return;
        }

        console.error(
          "Erro ao buscar observations de escalonamento:",
          error,
        );
        setErroObservations(
          getApiErrorMessage(
            error,
            "Não foi possível carregar o histórico do escalonamento.",
          ),
        );
      } finally {
        if (
          requestId ===
          observationsRequestIdRef.current
        ) {
          setCarregandoObservations(false);
        }
      }
    })();
  }, [escalationWorkItem]);

  async function carregarMaisObservations() {
    if (
      escalationWorkItem === null ||
      nextCursor === null ||
      carregandoMaisObservations
    ) {
      return;
    }

    const workItemId = escalationWorkItem.id;
    const requestId =
      ++observationsRequestIdRef.current;
    setCarregandoMaisObservations(true);

    try {
      const page = await fetchEscalationObservations(
        workItemId,
        { afterId: nextCursor },
      );

      if (
        requestId !==
        observationsRequestIdRef.current
      ) {
        return;
      }

      // Append preserva a ordem da API (id ASC). Sem deduplicacao
      // manual: after_id garante que a pagina seguinte so contem
      // id > nextCursor, entao o conjunto anexado e sempre disjunto
      // do que ja esta em `observations`.
      setObservations((anterior) => [
        ...anterior,
        ...page.items,
      ]);
      setNextCursor(page.next_cursor);
    } catch (error) {
      if (
        requestId !==
        observationsRequestIdRef.current
      ) {
        return;
      }

      console.error(
        "Erro ao carregar mais observations:",
        error,
      );
      setErroObservations(
        getApiErrorMessage(
          error,
          "Não foi possível carregar mais observações.",
        ),
      );
    } finally {
      if (
        requestId ===
        observationsRequestIdRef.current
      ) {
        setCarregandoMaisObservations(false);
      }
    }
  }

  async function solicitarEscalonamento() {
    setMaterializando(true);
    setErroMaterializacao("");

    try {
      await api.post(
        "/recommendations/human-escalation/" +
          `accounts/${accountId}/episodes/${dueDate}/materialize`,
      );

      refetchEscalationWorkItem();
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
      // Fotografia sempre vem de GET (refetch), nunca do corpo do
      // POST -- mesmo padrão já usado por executarMarkOverdue, agora
      // unificado: uma única fonte de verdade para o estado exibido.
      await api.post<MarkOverdueMaterializationResponse>(
        "/recommendations/mark-overdue/" +
          `accounts/${accountId}/episodes/${dueDate}/materialize`,
      );

      refetchMarkOverdueWorkItem();
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
      refetchMarkOverdueWorkItem();
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

  const escalonamentoTerminal =
    escalationWorkItem !== null &&
    (escalationWorkItem.status === "completed" ||
      escalationWorkItem.status === "cancelled");

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
                        escalationWorkItem !== null
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

      {carregandoEscalationWorkItem && (
        <div className="recomendacoes-detail-loading">
          <div className="loading-spinner" />
          <span aria-live="polite">
            Carregando estado do escalonamento...
          </span>
        </div>
      )}

      {!carregandoEscalationWorkItem &&
        erroEscalationWorkItem && (
          <div className="error-message recommendation-materialize-error">
            <AlertTriangle size={18} />
            <span>{erroEscalationWorkItem}</span>
          </div>
        )}

      {!carregandoEscalationWorkItem &&
        !erroEscalationWorkItem &&
        escalationWorkItem !== null && (
          <div className="recommendation-escalation-panel">
            <div className="recommendation-materialize-result">
              <Check size={18} />
              <span>
                Escalonamento{" "}
                {escalonamentoTerminal
                  ? "encerrado"
                  : "em andamento"}
                {" "}-- status: {escalationWorkItem.status}.
              </span>
            </div>

            {carregandoObservations && (
              <div className="recomendacoes-detail-loading">
                <div className="loading-spinner" />
                <span aria-live="polite">
                  Carregando histórico...
                </span>
              </div>
            )}

            {!carregandoObservations &&
              erroObservations && (
                <div className="error-message recommendation-materialize-error">
                  <AlertTriangle size={18} />
                  <span>{erroObservations}</span>
                </div>
              )}

            {!carregandoObservations &&
              !erroObservations &&
              observations.length === 0 && (
                <p className="recommendation-observations-empty">
                  Nenhuma observação registrada
                  ainda.
                </p>
              )}

            {!carregandoObservations &&
              !erroObservations &&
              observations.length > 0 && (
                <ul className="recommendation-observations-list">
                  {observations.map(
                    (observation) => (
                      <li
                        key={observation.id}
                        className="recommendation-observation-item"
                      >
                        {observation.observation_type ===
                        "observed_fact" ? (
                          <>
                            <FileText
                              size={16}
                              aria-hidden="true"
                            />
                            <span>
                              Pagamento registrado
                              em{" "}
                              {formatarDataHora(
                                observation.observed_at,
                              )}
                              .
                            </span>
                          </>
                        ) : (
                          <>
                            <ClipboardList
                              size={16}
                              aria-hidden="true"
                            />
                            <span>
                              {ASSESSMENT_CODE_LABELS[
                                observation
                                  .assessment_code
                              ] ??
                                observation.assessment_code}
                              {" "}
                              em{" "}
                              {formatarDataHora(
                                observation.declared_at,
                              )}
                              .
                            </span>
                          </>
                        )}
                      </li>
                    ),
                  )}
                </ul>
              )}

            {nextCursor !== null && (
              <button
                type="button"
                className="secondary-button"
                disabled={
                  carregandoMaisObservations
                }
                onClick={() =>
                  void carregarMaisObservations()
                }
              >
                {carregandoMaisObservations
                  ? "Carregando..."
                  : "Carregar mais"}
              </button>
            )}
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
                  : traduzirRazao(action.reason) ??
                    "Sem motivo informado pelo servidor."}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
