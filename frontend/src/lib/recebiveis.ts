import type { BotMood } from "../components/ui";
import type { Account } from "../types/account";
import { diasAte } from "./cockpit";

export type FaixaChave = "a-vencer" | "1-30" | "31-60" | "61-90" | "mais-90";
export type AbaChave = "todas" | "vencidas" | "ate-3-dias";

export const JANELA_PROXIMAS = 3;

export const FAIXAS: ReadonlyArray<{ chave: FaixaChave; rotulo: string }> = [
  { chave: "a-vencer", rotulo: "A vencer" },
  { chave: "1-30", rotulo: "1 a 30 dias" },
  { chave: "31-60", rotulo: "31 a 60 dias" },
  { chave: "61-90", rotulo: "61 a 90 dias" },
  { chave: "mais-90", rotulo: "Mais de 90 dias" },
];

export interface ContaEmAberto {
  conta: Account;
  valor: number;
  /** Dias de atraso (> 0) quando vencida; 0 caso contrário. */
  diasAtraso: number;
  /** Dias até o vencimento (>= 0) quando ainda não vencida; 0 caso contrário. */
  diasParaVencer: number;
  vencida: boolean;
  faixa: FaixaChave;
  mood: BotMood;
}

export interface ResumoFaixa {
  chave: FaixaChave;
  rotulo: string;
  valor: number;
  faturas: number;
}

export interface ResumoRecebiveis {
  total: number;
  faturas: number;
  faixas: ResumoFaixa[];
}

export function estaEmAberto(conta: Account): boolean {
  return conta.status !== "pago";
}

export function faixaPorAtraso(diasAtraso: number): FaixaChave {
  if (diasAtraso <= 0) return "a-vencer";
  if (diasAtraso <= 30) return "1-30";
  if (diasAtraso <= 60) return "31-60";
  if (diasAtraso <= 90) return "61-90";
  return "mais-90";
}

export function humorPorSituacao(diasAtraso: number, diasParaVencer: number): BotMood {
  if (diasAtraso > 30) return "critico";
  if (diasAtraso >= 1) return "preocupado";
  if (diasParaVencer <= JANELA_PROXIMAS) return "atento";
  return "feliz";
}

/** Classifica contas em aberto; pagas e datas inválidas ficam de fora. */
export function classificarContas(contas: Account[], hoje: Date): ContaEmAberto[] {
  const itens: ContaEmAberto[] = [];

  for (const conta of contas) {
    if (!estaEmAberto(conta)) continue;
    const dias = diasAte(conta.vencimento, hoje);
    if (dias === null) continue;

    const diasAtraso = dias < 0 ? -dias : 0;
    const diasParaVencer = dias >= 0 ? dias : 0;

    itens.push({
      conta,
      valor: Number(conta.valor) || 0,
      diasAtraso,
      diasParaVencer,
      vencida: diasAtraso > 0,
      faixa: faixaPorAtraso(diasAtraso),
      mood: humorPorSituacao(diasAtraso, diasParaVencer),
    });
  }

  return itens;
}

export function resumirFaixas(itens: ContaEmAberto[]): ResumoRecebiveis {
  const faixas: ResumoFaixa[] = FAIXAS.map((f) => ({ ...f, valor: 0, faturas: 0 }));

  for (const item of itens) {
    const faixa = faixas.find((f) => f.chave === item.faixa);
    if (faixa) {
      faixa.valor += item.valor;
      faixa.faturas += 1;
    }
  }

  return {
    total: faixas.reduce((soma, f) => soma + f.valor, 0),
    faturas: faixas.reduce((soma, f) => soma + f.faturas, 0),
    faixas,
  };
}

/** Vencidas por atraso decrescente; depois as que vencem, por prazo crescente. */
export function ordenarFila(itens: ContaEmAberto[]): ContaEmAberto[] {
  return [...itens].sort((a, b) => {
    if (a.vencida !== b.vencida) return a.vencida ? -1 : 1;
    if (a.vencida) {
      if (a.diasAtraso !== b.diasAtraso) return b.diasAtraso - a.diasAtraso;
    } else if (a.diasParaVencer !== b.diasParaVencer) {
      return a.diasParaVencer - b.diasParaVencer;
    }
    if (a.valor !== b.valor) return b.valor - a.valor;
    return a.conta.id - b.conta.id;
  });
}

export function venceEmAte3Dias(item: ContaEmAberto): boolean {
  return !item.vencida && item.diasParaVencer <= JANELA_PROXIMAS;
}

/** Itens da fila de cobrança para a aba escolhida, já ordenados. */
export function filtrarFila(itens: ContaEmAberto[], aba: AbaChave): ContaEmAberto[] {
  const ordenados = ordenarFila(itens);

  if (aba === "vencidas") return ordenados.filter((i) => i.vencida);
  if (aba === "ate-3-dias") return ordenados.filter(venceEmAte3Dias);
  return ordenados.filter((i) => i.vencida || venceEmAte3Dias(i));
}

export function contarAbas(itens: ContaEmAberto[]): Record<AbaChave, number> {
  return {
    todas: filtrarFila(itens, "todas").length,
    vencidas: filtrarFila(itens, "vencidas").length,
    "ate-3-dias": filtrarFila(itens, "ate-3-dias").length,
  };
}

export function descreverAtraso(dias: number): string {
  return dias === 1 ? "1 dia em atraso" : `${dias} dias em atraso`;
}
