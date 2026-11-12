import type { BotMood } from "../components/ui";
import type { DashboardData } from "../types/dashboard";

export interface ItemAtencao {
  cliente: string;
  valor: number;
  /** "atraso": conta vencida pela regra do backend; "vence": vence em até 7 dias. */
  tipo: "atraso" | "vence";
  /** Dias até o vencimento, só para tipo "vence". */
  dias?: number;
}

export interface Cockpit {
  mood: BotMood;
  titulo: string;
  detalhe: string;
  aReceber: number;
  emAtraso: number;
  aVencer: number;
  recebido: number;
  faturamento: number;
  contasEmAtraso: number;
  /** Fração de "a receber" que está em atraso (0 a 1). */
  parteEmAtraso: number;
  /** Fração do faturamento já recebida (0 a 1). */
  parteRecebida: number;
  itens: ItemAtencao[];
}

const LIMITE_ITENS = 5;
const JANELA_DIAS = 7;
const MS_POR_DIA = 86_400_000;

export function formatarMoeda(valor: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  }).format(valor);
}

/** Dias inteiros entre hoje e uma data "AAAA-MM-DD", no fuso local. */
export function diasAte(data: string, hoje: Date): number | null {
  const [ano, mes, dia] = data.slice(0, 10).split("-").map(Number);

  if (!ano || !mes || !dia) {
    return null;
  }

  const alvo = new Date(ano, mes - 1, dia);
  const base = new Date(hoje.getFullYear(), hoje.getMonth(), hoje.getDate());

  return Math.round((alvo.getTime() - base.getTime()) / MS_POR_DIA);
}

export function descreverPrazo(dias: number): string {
  if (dias === 0) return "Vence hoje";
  if (dias === 1) return "Vence amanhã";
  return `Vence em ${dias} dias`;
}

function plural(n: number, um: string, varios: string): string {
  return n === 1 ? um : varios;
}

export function montarCockpit(dados: DashboardData, hoje: Date): Cockpit {
  const { resumo, indicadores, alertas, vencimentos } = dados;

  const aReceber = Number(resumo.pendente) || 0;
  const emAtraso = Number(resumo.atrasado) || 0;
  const recebido = Number(resumo.recebido) || 0;
  const faturamento = Number(resumo.faturamento_total) || 0;
  const contasEmAtraso = indicadores.clientes_atrasados;

  const atrasados: ItemAtencao[] = [...alertas]
    .sort((a, b) => b.valor - a.valor)
    .map((alerta) => ({
      cliente: alerta.cliente,
      valor: Number(alerta.valor) || 0,
      tipo: "atraso",
    }));

  const proximos: ItemAtencao[] = vencimentos
    .filter((v) => v.status.toLowerCase() !== "pago")
    .map((v) => ({
      cliente: v.cliente,
      valor: Number(v.valor) || 0,
      tipo: "vence" as const,
      dias: diasAte(v.vencimento, hoje) ?? -1,
    }))
    .filter((v) => v.dias >= 0 && v.dias <= JANELA_DIAS)
    .sort((a, b) => a.dias - b.dias);

  let mood: BotMood;
  let titulo: string;
  let detalhe: string;

  if (aReceber <= 0) {
    mood = "dormindo";
    titulo = "Nada pendente.";
    detalhe = "Todas as contas cadastradas estão pagas.";
  } else if (emAtraso > 0) {
    mood = "preocupado";
    titulo = `${contasEmAtraso} ${plural(contasEmAtraso, "conta está", "contas estão")} em atraso, somando ${formatarMoeda(emAtraso)}.`;
    const maior = atrasados[0];
    const empate = atrasados.length > 1 && atrasados[1].valor === maior?.valor;
    detalhe = !maior
      ? "Confira as contas em atraso na lista de clientes."
      : empate
        ? `Comece por ${maior.cliente}, de ${formatarMoeda(maior.valor)}.`
        : `A maior é de ${maior.cliente}, ${formatarMoeda(maior.valor)}. Vale começar por ela.`;
  } else if (proximos.length > 0) {
    const soma = proximos.reduce((total, v) => total + v.valor, 0);
    mood = proximos.some((v) => (v.dias ?? 99) <= 3) ? "atento" : "feliz";
    titulo = `${proximos.length} ${plural(proximos.length, "conta vence", "contas vencem")} nos próximos ${JANELA_DIAS} dias, somando ${formatarMoeda(soma)}.`;
    detalhe = "Nada em atraso por enquanto.";
  } else {
    mood = "feliz";
    titulo = "Nenhuma conta em atraso.";
    detalhe = `Há ${formatarMoeda(aReceber)} a receber, sem vencimentos nos próximos ${JANELA_DIAS} dias.`;
  }

  return {
    mood,
    titulo,
    detalhe,
    aReceber,
    emAtraso,
    aVencer: Math.max(aReceber - emAtraso, 0),
    recebido,
    faturamento,
    contasEmAtraso,
    parteEmAtraso: aReceber > 0 ? emAtraso / aReceber : 0,
    parteRecebida: faturamento > 0 ? recebido / faturamento : 0,
    itens: [...atrasados, ...proximos].slice(0, LIMITE_ITENS),
  };
}
