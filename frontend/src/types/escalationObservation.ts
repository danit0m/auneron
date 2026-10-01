export type AssessmentCode =
  | "contact_made"
  | "payment_promised"
  | "payment_refused"
  | "unreachable"
  | "partial_agreement";

export interface ObservedFactObservationResponse {
  observation_type: "observed_fact";
  id: number;
  escalation_work_item_id: number;
  created_at: string;
  linked_account_event_id: number;
  observed_at: string;
}

export interface HumanAssessmentObservationResponse {
  observation_type: "human_assessment";
  id: number;
  escalation_work_item_id: number;
  created_at: string;
  assessment_code: AssessmentCode;
  declared_by_user_id: number;
  declared_at: string;
}

export type EscalationObservationResponse =
  | ObservedFactObservationResponse
  | HumanAssessmentObservationResponse;

export interface EscalationObservationListResponse {
  items: EscalationObservationResponse[];
  next_cursor: number | null;
}
