import type { Account } from "../types/account";
import { dataIsoLocal, dataIsoValida } from "./pagamento";

/**
 * Indicadores de recebimento (A4). Tudo é calculado sobre dias civis
 * AAAA-MM-DD, sem passar por `new Date("AAAA-MM-DD")` (que desloca em UTC).
 * Só entram títulos `pago` com `paid_on` válido; os pagos sem data ficam
 * à parte e fora das médias.
 */

/** Janela do "Recebido nos últimos 30 dias": hoje e os 30 dias anteriores (inclusive). */
export const JANELA_RECEBIDO_DIAS = 30;

const MS_POR_DIA = 86_400_000;

export interface RecebidoNoPeriodo {
  valor: number;
  quantidade: number;
  dias: number;
}

export interface AtrasoMedioPagamento {
  /** Média de (paid_on − vencimento) em dias; negativo = antes do vencimento. */
  mediaExata: number;
  /** Média arredondada ao inteiro mais próximo (meios afastam-se do zero). */
  mediaDias: number;
  emDia: number;
  comAtraso: number;
  /** Títulos considerados (pagos com data e vencimento válidos). */
  total: number;
}

export interface PagosSemData {
  quantidade: number;
  valor: number;
}

function valorDe(conta: Account): number {
  return Number(conta.valor) || 0;
}

/** Dia civil AAAA-MM-DD → dias desde 1970 (UTC, sem efeito de fuso/DST). */
function diaCivil(iso: string): number {
  const [ano, mes, dia] = iso.split("-").map(Number);
  return Math.round(Date.UTC(ano, mes - 1, dia) / MS_POR_DIA);
}

function dataPagamento(conta: Account): string | null {
  if (conta.status !== "pago") return null;
  const data = conta.paid_on;
  return data && dataIsoValida(data) ? data : null;
}

function dataVencimento(conta: Account): string | null {
  const dia = (conta.vencimento ?? "").slice(0, 10);
  return dataIsoValida(dia) ? dia : null;
}

/** Pagos com data efetiva válida; abertos/atrasados nunca entram, mesmo com paid_on. */
function pagosComData(contas: Account[]): Array<{ conta: Account; pagoEm: string }> {
  const itens: Array<{ conta: Account; pagoEm: string }> = [];
  for (const conta of contas) {
    const pagoEm = dataPagamento(conta);
    if (pagoEm) itens.push({ conta, pagoEm });
  }
  return itens;
}

/**
 * Soma e quantidade dos títulos pagos com paid_on entre (hoje − dias) e hoje,
 * ambos inclusive. null quando não há nenhum pagamento com data (a tela não
 * inventa zero); havendo pagamentos datados fora da janela, o zero é real.
 */
export function recebidoNoPeriodo(
  contas: Account[],
  hoje: Date,
  dias: number = JANELA_RECEBIDO_DIAS,
): RecebidoNoPeriodo | null {
  const pagos = pagosComData(contas);
  if (pagos.length === 0) return null;

  const fim = diaCivil(dataIsoLocal(hoje));
  const inicio = fim - dias;

  let valor = 0;
  let quantidade = 0;
  for (const { conta, pagoEm } of pagos) {
    const dia = diaCivil(pagoEm);
    if (dia < inicio || dia > fim) continue;
    valor += valorDe(conta);
    quantidade += 1;
  }

  return { valor, quantidade, dias };
}

function arredondar(valor: number): number {
  const r = Math.round(Math.abs(valor));
  return valor < 0 && r !== 0 ? -r : r;
}

/**
 * Atraso médio no pagamento: média de (paid_on − vencimento) em dias, sobre
 * os pagos com data. Negativos contam como estão. null sem nenhum título.
 */
export function atrasoMedioPagamento(contas: Account[]): AtrasoMedioPagamento | null {
  let soma = 0;
  let total = 0;
  let emDia = 0;
  let comAtraso = 0;

  for (const { conta, pagoEm } of pagosComData(contas)) {
    const venc = dataVencimento(conta);
    if (!venc) continue;
    const diff = diaCivil(pagoEm) - diaCivil(venc);
    soma += diff;
    total += 1;
    if (diff <= 0) emDia += 1;
    else comAtraso += 1;
  }

  if (total === 0) return null;

  const mediaExata = soma / total;
  return { mediaExata, mediaDias: arredondar(mediaExata), emDia, comAtraso, total };
}

/** Títulos pagos sem data efetiva registrada (anteriores ao registro de paid_on). */
export function pagosSemData(contas: Account[]): PagosSemData | null {
  let quantidade = 0;
  let valor = 0;
  for (const conta of contas) {
    if (conta.status !== "pago" || dataPagamento(conta)) continue;
    quantidade += 1;
    valor += valorDe(conta);
  }
  return quantidade === 0 ? null : { quantidade, valor };
}

export function descreverAtrasoMedio(dias: number): string {
  if (dias === 0) return "no vencimento";
  const n = Math.abs(dias);
  const unidade = n === 1 ? "1 dia" : `${n} dias`;
  return dias > 0 ? `${unidade} depois do vencimento` : `${unidade} antes do vencimento`;
}

export function descreverPagosEmDia(emDia: number, total: number): string {
  return `${emDia} de ${total}`;
}

export function descreverTitulosPagos(n: number): string {
  return n === 1 ? "1 título" : `${n} títulos`;
}

export function descreverSemData(info: PagosSemData, valorFormatado: string): string {
  const quantos =
    info.quantidade === 1
      ? "1 título pago sem data registrada"
      : `${info.quantidade} títulos pagos sem data registrada`;
  return `${quantos} (${valorFormatado}) ${info.quantidade === 1 ? "fica" : "ficam"} fora das médias.`;
}
