export interface OrchestratorHealth {
  status: string;
  orchestrator: string;
  registered_events: number;
  registered_agents: number;
  executions: number;
  successes: number;
  failures: number;
  success_rate: number;
  stored_decisions?: number;
}

export interface RegisteredAgent {
  name: string;
  priority: number;
}

export interface OrchestratorRegistry {
  events: Record<
    string,
    RegisteredAgent[]
  >;
  registered_events: number;
  registered_agents: number;
}

export interface AgentMetrics {
  agent_name: string;
  executions: number;
  successes: number;
  failures: number;
  total_duration_seconds: number;
  average_duration_seconds: number;
  success_rate: number;
}

export interface MetricsSummary {
  registered_agents: number;
  executions: number;
  successes: number;
  failures: number;
  success_rate: number;
  average_duration_seconds: number;
  total_duration_seconds: number;
}

export interface OrchestratorMetrics {
  summary: MetricsSummary;
  agents: AgentMetrics[];
}

export type TelemetryStatus =
  | "SUCCESS"
  | "ERROR";

export interface TelemetryRecord {
  telemetry_id: string;
  event_name: string;
  strategy_name: string;
  agent_name: string;
  priority: number;
  status: TelemetryStatus;
  duration_seconds: number;
  started_at: string;
  finished_at: string;
  error_type: string | null;
  error_message: string | null;
}

export interface OrchestratorTelemetry {
  total_returned: number;
  limit: number;
  filters: {
    agent_name: string | null;
    status: string | null;
  };
  records: TelemetryRecord[];
}

/* =====================================
   DECISION ENGINE
===================================== */

export interface DecisionSignal {
  name: string;
  value: string | number | boolean | null;
  description: string;
}

export interface DecisionContext {
  cliente: string;
  valor: number;
  status: string;
  vencimento: string;
  dias_atraso: number;
}

export interface StoredDecision {
  decision_id: string;
  event_name: string;
  decision_name: string;
  reason: string;
  confidence: number;
  confidence_percentage: number;
  selected_agents: string[];
  signals: DecisionSignal[];
  context: DecisionContext;
  created_at: string;
}

export interface LatestDecisionResponse {
  available: boolean;
  decision: StoredDecision | null;
}

export interface DecisionHistoryResponse {
  total_returned: number;
  stored_decisions: number;
  limit: number;
  filters: {
    decision_name: string | null;
    event_name: string | null;
  };
  records: StoredDecision[];
}

export interface DecisionRule {
  name: string;
  priority: number;
  class_name: string;
}

export interface DecisionRulesResponse {
  total: number;
  rules: DecisionRule[];
}

/* =====================================
   TIPOS AUXILIARES DO DASHBOARD
===================================== */

export interface AgentRegistryItem
  extends RegisteredAgent {
  eventName: string;
}

export interface DecisionSummary {
  name: string;
  confidence: number;
  cliente: string;
  valor: number;
  status: string;
  diasAtraso: number;
  motivo: string;
  agentes: string[];
  sinais: DecisionSignal[];
  data: string;
}