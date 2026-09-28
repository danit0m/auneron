export interface DashboardResumo {
  clientes_total: number;
  faturamento_total: number;
  recebido: number;
  pendente: number;
  atrasado: number;
}

export interface DashboardIndicadores {
  taxa_recebimento: string;
  ticket_medio: number;
  clientes_atrasados: number;
}

export interface StatusClientes {
  pago: number;
  aberto: number;
  atrasado: number;
}

export interface RankingCliente {
  cliente: string;
  valor: number;
  status: string;
}

export interface Alerta {
  cliente: string;
  mensagem: string;
  valor: number;
}

export interface Vencimento {
  cliente: string;
  valor: number;
  vencimento: string;
  status: string;
}

export interface DashboardData {
  resumo: DashboardResumo;
  indicadores: DashboardIndicadores;
  status_clientes: StatusClientes;
  ranking_clientes: RankingCliente[];
  alertas: Alerta[];
  vencimentos: Vencimento[];
}

export interface GovernedOperationsSummaryPeriod {
  start: string;
  end: string;
  days: number;
}

export interface AutonomousEffectVerificationSummary {
  verified: number;
  checked_other: number;
  not_yet_checked: number;
  total: number;
  verification_rate: number | null;
}

export interface GovernedOperationsSummary {
  period: GovernedOperationsSummaryPeriod;
  eligible_accounts_identified: number;
  autonomous_dispositions: number;
  human_governed_dispositions: number;
  autonomous_disposition_rate: number | null;
  autonomous_effect_verification: AutonomousEffectVerificationSummary;
  pending_overdue_accounts_now: number;
  estimate: null;
}