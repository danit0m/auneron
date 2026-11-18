export type AccountStatus =
  | "aberto"
  | "pago"
  | "atrasado";

export interface Account {
  id: number;
  cliente: string;
  email: string | null;
  whatsapp: string | null;
  valor: number;
  vencimento: string;
  status: AccountStatus;
  created_at?: string | null;
  /** Data efetiva do pagamento (AAAA-MM-DD); null se nao pago ou sem registro. */
  paid_on?: string | null;
  /** A5: data/hora do último contato registrado; null se nunca houve. */
  ultimo_contato_em?: string | null;
  /** A5: data prometida vigente (AAAA-MM-DD), calculada pela API. */
  promessa_vigente?: string | null;
  /** A5: promessa vigente com data anterior a hoje e título não pago. */
  promessa_vencida?: boolean;
}

export interface AccountCreate {
  cliente: string;
  email: string | null;
  whatsapp: string | null;
  valor: number;
  vencimento: string;
}

export interface AccountUpdate {
  cliente?: string;
  email?: string | null;
  whatsapp?: string | null;
  valor?: number;
  vencimento?: string;
}