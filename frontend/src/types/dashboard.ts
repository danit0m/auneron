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