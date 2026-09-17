export type ActionKey =
  | "account.mark_overdue"
  | "account.mark_paid"
  | "escalate_to_human";

export type DecisionType =
  | "single_action"
  | "action_bundle"
  | "no_action";

export type CapabilityKind =
  | "skill_action"
  | "work_action";

export type InitiationActor =
  | "agent"
  | "user"
  | "rbac_actor";

export type AuthorityModel =
  | "approval"
  | "rbac";

export interface FinancialEpisode {
  account_id: number;
  due_date: string;
}

export interface ActionEvaluation {
  action_key: string;
  capability_kind: CapabilityKind;
  eligibility_policy: string;
  structurally_available: boolean;
  system_recommendable: boolean;
  requires_external_fact: string | null;
  reason: string | null;
  initiation_actor: InitiationActor;
  authority_model: AuthorityModel;
  required_authority: string;
  execution_corridor: string;
}

export interface ActionSpaceEvaluationResponse {
  episode: FinancialEpisode;
  actions: ActionEvaluation[];
  recommendable_actions: string[];
  no_action: boolean;
}

export interface RecurrenceFacts {
  average_late_days: number | null;
  resolved_occurrences: number | null;
}

export interface ObservedFacts {
  days_overdue: number;
  amount: number;
  recurrence: RecurrenceFacts | null;
  active_escalation: boolean;
}

export interface CalibrationSnapshot {
  version: string;
  critical_overdue_reference_days: number;
  early_high_exposure_reference_day: number;
  absolute_high_value_reference: number;
  operational_cost_floor_reference: number;
}

export interface Decision {
  decision_type: DecisionType;
  selected_actions: string[];
}

export interface NbaDecisionEvidenceResponse {
  episode: FinancialEpisode;
  action_space: ActionSpaceEvaluationResponse;
  observed_facts: ObservedFacts;
  policy_version: string;
  calibration: CalibrationSnapshot;
  applied_rules: string[];
  decision: Decision;
}

/**
 * action_key é identidade server-derivada (ver ActionEvaluation.action_key)
 * -- isto é só rótulo de apresentação, nunca usado para inferir
 * comportamento.
 */
export const ACTION_LABELS: Record<string, string> = {
  "account.mark_overdue": "Marcar conta como atrasada",
  "account.mark_paid": "Marcar conta como paga",
  "escalate_to_human": "Escalar para atendimento humano",
};

export function getActionLabel(actionKey: string): string {
  return ACTION_LABELS[actionKey] ?? actionKey;
}

export interface HumanEscalationMaterializationResponseWorkItem {
  id: number;
  work_key: string | null;
  status: string;
}

export interface HumanEscalationMaterializationResponse {
  work_item: HumanEscalationMaterializationResponseWorkItem;
  created: boolean;
  duplicate: boolean;
}
