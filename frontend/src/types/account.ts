export interface Account {
  id: number;
  cliente: string;
  email: string;
  whatsapp: string;
  valor: number;
  vencimento: string;
  status: string;
  created_at: string;
}

export interface AccountCreate {
  cliente: string;
  email: string;
  whatsapp: string;
  valor: number;
  vencimento: string;
  status: string;
}

export interface AccountUpdate {
  cliente: string;
  email: string;
  whatsapp: string;
  valor: number;
  vencimento: string;
  status: string;
}