export type ApprovalStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "expired"
  | "cancelled";

export type ApprovalRiskLevel =
  | "low"
  | "medium"
  | "high"
  | "critical";

export type ApprovalDecisionValue =
  | "approved"
  | "rejected";

export interface ApprovalRequestResponse {
  request_id: number;
  action_type: "skill_execution";
  skill_version_id: number;
  skill_key: string;
  requester_actor_type:
    | "user"
    | "agent"
    | "system"
    | "integration";
  requester_user_id: number | null;
  risk_level: ApprovalRiskLevel;
  status: ApprovalStatus;
  target_account_id: number | null;
  target_user_id: number | null;
  expires_at: string;
  resolved_at: string | null;
  created_at: string;
}

export interface ApprovalDecisionResponse {
  decision_id: number;
  approval_request_id: number;
  decision: ApprovalDecisionValue;
  decided_by_user_id: number | null;
  decided_by_role: string;
  decision_note: string | null;
  created_at: string;
}

export interface ApprovalDetailsResponse {
  request: ApprovalRequestResponse;
  decision: ApprovalDecisionResponse | null;
}

export interface ApprovalListResponse {
  items: ApprovalRequestResponse[];
  next_cursor: number | null;
}

export interface ApprovalDecisionRequest {
  decision: ApprovalDecisionValue;
  decision_note?: string;
}

export interface ApprovalDecisionResultResponse {
  request: ApprovalRequestResponse;
  decision: ApprovalDecisionResponse;
}

/**
 * skill_key is a server-derived identity (see ApprovalRequestResponse.
 * skill_key) -- this is presentation only, never used to infer identity.
 */
export const APPROVAL_ACTION_LABELS: Record<string, string> = {
  "account.mark_overdue": "Marcar conta como atrasada",
};

export function getApprovalActionLabel(
  skillKey: string,
): string {
  return (
    APPROVAL_ACTION_LABELS[skillKey] ?? skillKey
  );
}
