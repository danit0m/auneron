import type { Account } from "../types/account";
import { diasAte } from "./cockpit";
import { classificarContas, estaEmAberto } from "./recebiveis";

/** Partição dos clientes: cada cliente cai em exatamente um grupo. */
export type FaixaCarteira = "em-dia" | "ate-30" | "mais-30" | "pagos";
/** Filtro da tabela: um grupo, os clientes sem contato com valor em aberto, ou nenhum. */
export type FiltroCarteira = FaixaCarteira | "sem-contato" | null;

export const LIMITE_ATE_30 = 30;
export const LIMITE_BARRAS = 4;
export const MIN_CLIENTES_CONCENTRACAO = 10;

export interface ClienteCarteira {
  /** Nome exibido (primeiro título, sem espaços nas pontas). */
  nome: string;
  contas: Account[];
  /** Soma de todos os títulos, pagos ou não. */
  valorTotal: number;
  /** Soma dos títulos em aberto (vencidos ou não). */
  valorAberto: number;
  /** Soma dos títulos em aberto ainda não vencidos. */
  valorEmAberto: number;
  /** Soma dos títulos em aberto vencidos. */
  valorVencido: number;
  /** Soma dos títulos pagos. */
  valorPago: number;
  temAberto: boolean;
  /** Maior atraso em dias entre os títulos em aberto (0 se nenhum vencido). */
  piorAtraso: number;
  faixa: FaixaCarteira;
  semContato: boolean;
}

export interface CartaoFaixa {
  faixa: FaixaCarteira;
  clientes: number;
  /** Em dia: valor em aberto. Atrasos: valor vencido. Pagos: valor pago. */
  valor: number;
}

export interface BarraConcentracao {
  nome: string;
  percentual: number;
}

export interface Concentracao {
  totalAberto: number;
  /** Quantos clientes compõem o número grande (até 3). */
  quantosNoTopo: number;
  percentualTopo: number;
  barras: BarraConcentracao[];
  outros: { clientes: number; percentual: number } | null;
}

export interface SemContato {
  /** Clientes sem contato que têm valor em aberto. */
  clientes: number;
}

export interface SaudeCarteiraDados {
  faixas: Record<FaixaCarteira, CartaoFaixa>;
  concentracao: Concentracao | null;
  semContato: SemContato | null;
}

export interface SituacaoConta {
  tipo: "atrasado" | "aberto" | "pago";
  dias: number;
  texto: string;
}

function vazio(valor: string | null | undefined): boolean {
  return !valor || valor.trim() === "";
}

function numero(valor: number): number {
  return Number(valor) || 0;
}

function somar(contas: Account[]): number {
  return contas.reduce((soma, c) => soma + numero(c.valor), 0);
}

export function faixaPorPiorAtraso(dias: number): "em-dia" | "ate-30" | "mais-30" {
  if (dias <= 0) return "em-dia";
  if (dias <= LIMITE_ATE_30) return "ate-30";
  return "mais-30";
}

/** Sem e-mail nem WhatsApp (vazio, null ou só espaços). */
export function contaSemContato(conta: Account): boolean {
  return vazio(conta.email) && vazio(conta.whatsapp);
}

/** Regra única de atraso: em aberto com vencimento antes de hoje. */
export function situacaoConta(conta: Account, hoje: Date): SituacaoConta {
  if (!estaEmAberto(conta)) return { tipo: "pago", dias: 0, texto: "Pago" };

  const dias = diasAte(conta.vencimento, hoje);
  if (dias !== null && dias < 0) {
    const atraso = -dias;
    return {
      tipo: "atrasado",
      dias: atraso,
      texto: `Atrasado · ${atraso} ${atraso === 1 ? "dia" : "dias"}`,
    };
  }
  return { tipo: "aberto", dias: 0, texto: "Em aberto" };
}

/** Agrupa títulos por cliente (nome sem espaços nas pontas, sem diferenciar maiúsculas). */
export function agruparClientes(contas: Account[], hoje: Date): ClienteCarteira[] {
  const grupos = new Map<string, Account[]>();

  for (const conta of contas) {
    const chave = conta.cliente.trim().toLowerCase();
    const lista = grupos.get(chave);
    if (lista) lista.push(conta);
    else grupos.set(chave, [conta]);
  }

  const clientes: ClienteCarteira[] = [];

  for (const lista of grupos.values()) {
    const itens = classificarContas(lista, hoje);
    let valorEmAberto = 0;
    let valorVencido = 0;
    let piorAtraso = 0;

    for (const item of itens) {
      if (item.vencida) {
        valorVencido += item.valor;
        if (item.diasAtraso > piorAtraso) piorAtraso = item.diasAtraso;
      } else {
        valorEmAberto += item.valor;
      }
    }

    const abertas = lista.filter(estaEmAberto);
    const temAberto = abertas.length > 0;

    clientes.push({
      nome: lista[0].cliente.trim(),
      contas: lista,
      valorTotal: somar(lista),
      valorAberto: somar(abertas),
      valorEmAberto,
      valorVencido,
      valorPago: somar(lista.filter((c) => !estaEmAberto(c))),
      temAberto,
      piorAtraso,
      faixa: temAberto ? faixaPorPiorAtraso(piorAtraso) : "pagos",
      semContato: lista.every(contaSemContato),
    });
  }

  return clientes;
}

export function resumirFaixasCarteira(
  clientes: ClienteCarteira[],
): Record<FaixaCarteira, CartaoFaixa> {
  const faixas: Record<FaixaCarteira, CartaoFaixa> = {
    "em-dia": { faixa: "em-dia", clientes: 0, valor: 0 },
    "ate-30": { faixa: "ate-30", clientes: 0, valor: 0 },
    "mais-30": { faixa: "mais-30", clientes: 0, valor: 0 },
    pagos: { faixa: "pagos", clientes: 0, valor: 0 },
  };

  for (const c of clientes) {
    const cartao = faixas[c.faixa];
    cartao.clientes += 1;
    cartao.valor +=
      c.faixa === "em-dia"
        ? c.valorEmAberto
        : c.faixa === "pagos"
          ? c.valorPago
          : c.valorVencido;
  }

  return faixas;
}

/** Concentração do valor EM ABERTO; só com 10 clientes ou mais e valor em aberto > 0. */
export function calcularConcentracao(clientes: ClienteCarteira[]): Concentracao | null {
  if (clientes.length < MIN_CLIENTES_CONCENTRACAO) return null;

  const total = clientes.reduce((soma, c) => soma + c.valorAberto, 0);
  if (total <= 0) return null;

  const ordenados = clientes
    .filter((c) => c.valorAberto > 0)
    .sort((a, b) => b.valorAberto - a.valorAberto || a.nome.localeCompare(b.nome, "pt-BR"));
  const pct = (valor: number) => Math.round((valor / total) * 100);

  const topo = ordenados.slice(0, 3);
  const resto = ordenados.slice(LIMITE_BARRAS);

  return {
    totalAberto: total,
    quantosNoTopo: topo.length,
    percentualTopo: pct(topo.reduce((soma, c) => soma + c.valorAberto, 0)),
    barras: ordenados
      .slice(0, LIMITE_BARRAS)
      .map((c) => ({ nome: c.nome, percentual: pct(c.valorAberto) })),
    outros:
      resto.length > 0
        ? {
            clientes: resto.length,
            percentual: pct(resto.reduce((soma, c) => soma + c.valorAberto, 0)),
          }
        : null,
  };
}

function ehSemContatoComAberto(c: ClienteCarteira): boolean {
  return c.semContato && c.valorAberto > 0;
}

export function calcularSemContato(clientes: ClienteCarteira[]): SemContato | null {
  const n = clientes.filter(ehSemContatoComAberto).length;
  return n > 0 ? { clientes: n } : null;
}

export function montarSaudeCarteira(contas: Account[], hoje: Date): SaudeCarteiraDados | null {
  if (contas.length === 0) return null;
  const clientes = agruparClientes(contas, hoje);

  return {
    faixas: resumirFaixasCarteira(clientes),
    concentracao: calcularConcentracao(clientes),
    semContato: calcularSemContato(clientes),
  };
}

/** Títulos dos clientes do grupo escolhido; sem filtro devolve tudo. */
export function filtrarContas(
  contas: Account[],
  hoje: Date,
  filtro: FiltroCarteira,
): Account[] {
  if (filtro === null) return contas;

  const ids = new Set<number>();
  for (const c of agruparClientes(contas, hoje)) {
    const dentro = filtro === "sem-contato" ? ehSemContatoComAberto(c) : c.faixa === filtro;
    if (dentro) for (const conta of c.contas) ids.add(conta.id);
  }
  return contas.filter((conta) => ids.has(conta.id));
}

export function pluralizarClientes(n: number): string {
  return n === 1 ? "1 cliente" : `${n} clientes`;
}

/** Linha de resumo do cabeçalho: número de clientes e soma de todos os títulos. */
export function resumirCadastro(
  contas: Account[],
): { clientes: number; valorCadastrado: number } {
  const chaves = new Set(contas.map((c) => c.cliente.trim().toLowerCase()));
  return { clientes: chaves.size, valorCadastrado: somar(contas) };
}
