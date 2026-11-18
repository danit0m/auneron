import type { Account } from "../types/account";

export type TipoEventoContato =
  | "contato"
  | "promessa"
  | "remarcacao"
  | "encerramento";

export const MAX_NOTA = 280;

export const MSG_DATA_PROMESSA_OBRIGATORIA = "Informe a data prometida.";
export const MSG_DATA_PROMESSA_INVALIDA = "Data inválida.";
export const MSG_NOTA_LONGA = `A observação deve ter no máximo ${MAX_NOTA} caracteres.`;

export const ROTULO_TIPO: Record<TipoEventoContato, string> = {
  contato: "Registrar contato",
  promessa: "Registrar promessa de pagamento",
  remarcacao: "Remarcar promessa",
  encerramento: "Encerrar promessa",
};

const REGEX_ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

function dataIsoReal(valor: string): boolean {
  const partes = REGEX_ISO.exec(valor);
  if (!partes) return false;
  const ano = Number(partes[1]);
  const mes = Number(partes[2]);
  const dia = Number(partes[3]);
  const data = new Date(ano, mes - 1, dia);
  return data.getFullYear() === ano && data.getMonth() === mes - 1 && data.getDate() === dia;
}

function doisDigitos(n: number): string {
  return String(n).padStart(2, "0");
}

function dataBr(valor: string): string {
  const [ano, mes, dia] = valor.split("-");
  return `${dia}/${mes}/${ano}`;
}

/** Promessa vigente informada pela API (null/ausente quando não há). */
export function promessaVigente(conta: Pick<Account, "promessa_vigente">): string | null {
  return conta.promessa_vigente ?? null;
}

/** Selo "Promessa vencida": só vale o cálculo feito pela API (fuso de negócio). */
export function temPromessaVencida(conta: Pick<Account, "promessa_vencida" | "status">): boolean {
  return conta.promessa_vencida === true && conta.status !== "pago";
}

/** Tipos que o modal oferece: remarcar e encerrar só existem com promessa vigente. */
export function tiposDisponiveis(
  conta: Pick<Account, "promessa_vigente">,
): TipoEventoContato[] {
  return promessaVigente(conta)
    ? ["contato", "remarcacao", "encerramento"]
    : ["contato", "promessa"];
}

export function tipoExigeData(tipo: TipoEventoContato): boolean {
  return tipo === "promessa" || tipo === "remarcacao";
}

/** Texto da coluna "Último contato"; "—" quando nunca houve contato. */
export function descreverUltimoContato(valor: string | null | undefined): string {
  if (!valor) return "—";
  const data = new Date(valor);
  if (Number.isNaN(data.getTime())) return "—";
  return `${doisDigitos(data.getDate())}/${doisDigitos(data.getMonth() + 1)}/${data.getFullYear()}`;
}

/** Texto da coluna "Próxima promessa"; "—" quando não há promessa vigente. */
export function descreverProximaPromessa(
  conta: Pick<Account, "promessa_vigente">,
): string {
  const data = promessaVigente(conta);
  return data && dataIsoReal(data) ? dataBr(data) : "—";
}

export interface DadosEventoContato {
  tipo: TipoEventoContato;
  data: string;
  nota: string;
}

export type ResultadoEventoContato =
  | { valido: true; corpo: { event_type: TipoEventoContato; promised_date?: string; note?: string } }
  | { valido: false; erro: string };

/** Monta o corpo do POST; data só para promessa/remarcação, nota em branco some. */
export function montarEventoContato(dados: DadosEventoContato): ResultadoEventoContato {
  const nota = dados.nota.trim();
  if (nota.length > MAX_NOTA) return { valido: false, erro: MSG_NOTA_LONGA };

  const corpo: { event_type: TipoEventoContato; promised_date?: string; note?: string } = {
    event_type: dados.tipo,
  };

  if (tipoExigeData(dados.tipo)) {
    if (!dados.data) return { valido: false, erro: MSG_DATA_PROMESSA_OBRIGATORIA };
    if (!dataIsoReal(dados.data)) return { valido: false, erro: MSG_DATA_PROMESSA_INVALIDA };
    corpo.promised_date = dados.data;
  }

  if (nota) corpo.note = nota;

  return { valido: true, corpo };
}
