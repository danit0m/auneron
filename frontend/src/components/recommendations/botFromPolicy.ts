import type { BotMood } from "../ui";
import type {
  ActionEvaluation,
  ActionSpaceEvaluationResponse,
  Decision,
} from "../../types/nba";

/**
 * Apresentacao da decisao da politica deterministica no AuneronBot.
 *
 * O bot NUNCA decide: so traduz o que o backend ja decidiu
 * (app/core/governed_financial_action_eligibility.py). Esta funcao le
 * somente `recommendable_actions`, `no_action` e, por acao,
 * `system_recommendable`, `reason` e `requires_external_fact`. Nao le
 * dias de atraso, valor nem situacao de fatura.
 *
 * A mensagem vem de `decision.selected_actions` (a mesma acao que o
 * card mostra); o action_space e so evidencia e checagem. Fail-closed
 * (-> pensando): decision_type incoerente com a lista, duplicados, acao
 * fora de `recommendable_actions` ou sem `system_recommendable=true`,
 * acao desconhecida, ou invariante no_action == (recommendable_actions
 * vazia) violado. no_action: feliz (already_paid) / dormindo (demais);
 * o motivo so e anexado se conhecido.
 * account.mark_paid nao tem ramo proprio (nunca e recomendavel pelo sistema).
 */

/**
 * Traducao das razoes de inelegibilidade ja definidas no backend
 * (IneligibilityReason, app/core/human_escalation_eligibility.py) --
 * vocabulario fechado, nunca texto livre. Codigo desconhecido cai no
 * fallback ja existente ("Sem motivo informado pelo servidor."),
 * nunca quebra se o backend adicionar um codigo novo.
 */
export const INELIGIBILITY_REASON_LABELS: Record<
  string,
  string
> = {
  due_date_mismatch:
    "Data de vencimento não corresponde ao episódio atual.",
  account_paid: "Esta conta já foi paga.",
  lifecycle_not_overdue:
    "Esta conta não está mais em atraso.",
  active_escalation_exists:
    "Este episódio já tem um escalonamento em andamento.",
};

export function traduzirRazao(
  reason: string | null,
): string | null {
  if (reason === null) {
    return null;
  }

  return INELIGIBILITY_REASON_LABELS[reason] ?? reason;
}

/**
 * Motivos de account.mark_overdue (MarkOverdueReason no backend) que o
 * bot sabe apresentar quando no_action=true. Mapa proprio para nao
 * alterar o texto que o card ja mostra via traduzirRazao.
 */
const NO_ACTION_REASON_LABELS: Record<string, string> = {
  due_date_mismatch:
    INELIGIBILITY_REASON_LABELS.due_date_mismatch,
  already_paid: "Esta conta já foi paga.",
  status_not_open: "Esta conta não está em aberto.",
  not_overdue: "Esta conta ainda não está em atraso.",
};

export interface BotPresentation {
  mood: BotMood;
  message: string;
}

const NEUTRAL: BotPresentation = {
  mood: "pensando",
  message:
    "Não consegui interpretar a avaliação da política para este título. Consulte os detalhes abaixo.",
};

function findAction(
  space: ActionSpaceEvaluationResponse,
  key: string,
): ActionEvaluation | undefined {
  return space.actions.find((a) => a.action_key === key);
}

function isRecommended(
  space: ActionSpaceEvaluationResponse,
  key: string,
): boolean {
  return (
    space.recommendable_actions.includes(key) &&
    findAction(space, key)?.system_recommendable === true
  );
}

export function botFromPolicy(
  decision: Decision,
  space: ActionSpaceEvaluationResponse,
): BotPresentation {
  // Invariante do backend (action_space_evaluator.py):
  // no_action == (recommendable_actions vazia). Violacao -> fail-closed.
  if (
    space.no_action !==
    (space.recommendable_actions.length === 0)
  ) {
    return NEUTRAL;
  }

  const selected = decision.selected_actions;

  // decision_type precisa bater com selected_actions, sem duplicados.
  if (new Set(selected).size !== selected.length) {
    return NEUTRAL;
  }

  const coherent =
    (decision.decision_type === "no_action" &&
      selected.length === 0) ||
    (decision.decision_type === "single_action" &&
      selected.length === 1) ||
    (decision.decision_type === "action_bundle" &&
      selected.length >= 2);

  if (!coherent) {
    return NEUTRAL;
  }

  if (decision.decision_type === "no_action") {
    // A decisao diverge do space se ha acoes recomendaveis.
    if (space.recommendable_actions.length > 0) {
      return NEUTRAL;
    }

    const markOverdue = findAction(
      space,
      "account.mark_overdue",
    );

    if (markOverdue?.system_recommendable === true) {
      return NEUTRAL;
    }

    const reason = markOverdue?.reason ?? null;
    const label =
      reason !== null &&
      Object.hasOwn(NO_ACTION_REASON_LABELS, reason)
        ? NO_ACTION_REASON_LABELS[reason]
        : null;

    return {
      mood: reason === "already_paid" ? "feliz" : "dormindo",
      message:
        label === null
          ? "Nenhuma ação necessária."
          : `Nenhuma ação necessária. ${label}`,
    };
  }

  // Cada acao selecionada precisa estar no space como recomendavel.
  if (!selected.every((key) => isRecommended(space, key))) {
    return NEUTRAL;
  }

  const hasOverdue = selected.includes("account.mark_overdue");
  const hasEscalate = selected.includes("escalate_to_human");

  // Acao desconhecida selecionada: fail-closed.
  if (
    selected.length !==
    Number(hasOverdue) + Number(hasEscalate)
  ) {
    return NEUTRAL;
  }

  if (hasOverdue && hasEscalate) {
    return {
      mood: "critico",
      message:
        "A política indica marcar este título como vencido e encaminhá-lo para atendimento humano. A decisão é de quem aprova.",
    };
  }

  if (hasEscalate) {
    return {
      mood: "critico",
      message:
        "A política indica encaminhar este título para atendimento humano.",
    };
  }

  return {
    mood: "preocupado",
    message:
      "A política indica marcar este título como vencido. A decisão é de quem aprova.",
  };
}
