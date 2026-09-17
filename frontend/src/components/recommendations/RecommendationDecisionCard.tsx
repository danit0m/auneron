import {
  AlertTriangle,
  Check,
  Info,
} from "lucide-react";
import { useState } from "react";

import api, {
  getApiErrorMessage,
} from "../../api/api";
import type {
  HumanEscalationMaterializationResponse,
  NbaDecisionEvidenceResponse,
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

export default function RecommendationDecisionCard({
  accountId,
  dueDate,
  nba,
  podeMaterializarEscalonamento,
}: RecommendationDecisionCardProps) {
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
