import type { BotMood } from "../ui";
import type {
  ActionEvaluation,
  ActionSpaceEvaluationResponse,
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
 * Precedencia (varias acoes recomendaveis ao mesmo tempo):
 *   1. escalate_to_human    -> critico
 *   2. account.mark_overdue -> preocupado
 * Invariante do backend: no_action == (recommendable_actions vazia);
 * qualquer violacao (nas duas direcoes) -> pensando.
 * Com no_action=true: feliz (already_paid) / dormindo (motivo conhecido).
 * account.mark_paid nao tem ramo proprio (nunca e recomendavel pelo sistema).
 * Qualquer outra combinacao -> pensando (fail-closed). Uma acao so e dita
 * "recomendavel" se esta em `recommendable_actions` E tem
 * `system_recommendable=true`; qualquer divergencia cai em pensando.
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

  if (!space.no_action) {
    if (isRecommended(space, "escalate_to_human")) {
      return {
        mood: "critico",
        message:
          "A política indica encaminhar este título para atendimento humano.",
      };
    }

    if (isRecommended(space, "account.mark_overdue")) {
      return {
        mood: "preocupado",
        message:
          "A política indica marcar este título como vencido. A decisão é de quem aprova.",
      };
    }

    // Acao recomendavel desconhecida, ou lista sem
    // system_recommendable=true correspondente: fail-closed.
    return NEUTRAL;
  }

  if (space.no_action) {
    const markOverdue = findAction(
      space,
      "account.mark_overdue",
    );
    const reason = markOverdue?.reason ?? null;

    if (
      reason === null ||
      markOverdue?.system_recommendable === true ||
      !Object.hasOwn(NO_ACTION_REASON_LABELS, reason)
    ) {
      return NEUTRAL;
    }

    return {
      mood: reason === "already_paid" ? "feliz" : "dormindo",
      message: `Nenhuma ação necessária. ${NO_ACTION_REASON_LABELS[reason]}`,
    };
  }

  return NEUTRAL;
}
