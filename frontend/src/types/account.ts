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
}

export interface AccountCreate {
  cliente: string;
  email: string | null;
  whatsapp: string | null;
  valor: number;
  vencimento: string;
  status: AccountStatus;
}

export interface AccountUpdate {
  cliente?: string;
  email?: string | null;
  whatsapp?: string | null;
  valor?: number;
  vencimento?: string;
  status?: AccountStatus;
}