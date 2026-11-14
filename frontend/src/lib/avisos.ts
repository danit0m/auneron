import type { Account } from "../types/account";
import type { ApprovalRequestResponse } from "../types/approval";
import { estadoPagamento, podeConcluirPagamento } from "./pagamento";

/**
 * Lógica pura dos "Avisos ao operador" (sino do cabeçalho). Só mostra o que
 * foi realmente carregado: dado ausente (null) vira "sem aviso", nunca um
 * número inventado.
 */

/** Quantos pedidos aprovados têm o decisor conferido (1 chamada por pedido). */
export const MAX_PEDIDOS_CONFERIDOS = 10;

export const MSG_SEM_AVISOS = "Nada esperando por você.";

export interface ContagemConclusao {
  total: number;
  /** true quando havia mais candidatos que o limite conferido. */
  truncado: boolean;
}

export interface AvisoItem {
  chave: "aprovacoes" | "pagamentos";
  texto: string;
  destino: string;
}

/**
 * Pedidos account.mark_paid aprovados, não expirados, cujo título ainda não
 * está pago. Títulos fora da lista carregada ficam de fora (fail-closed).
 * Do mais recente para o mais antigo, no máximo `limite`.
 */
export function selecionarPedidosParaConcluir(
  aprovacoes: ApprovalRequestResponse[],
  contas: Pick<Account, "id" | "status">[],
  agora: Date,
  limite: number = MAX_PEDIDOS_CONFERIDOS,
): { pedidos: ApprovalRequestResponse[]; truncado: boolean } {
  const vistos = new Set<number>();
  const pedidos: ApprovalRequestResponse[] = [];

  for (const conta of contas) {
    if (conta.status === "pago") continue;
    const situacao = estadoPagamento(conta, aprovacoes, agora);
    if (situacao.estado === "aprovado-aguardando-conclusao" && situacao.pedido) {
      if (!vistos.has(situacao.pedido.request_id)) {
        vistos.add(situacao.pedido.request_id);
        pedidos.push(situacao.pedido);
      }
    }
  }

  pedidos.sort((a, b) => b.request_id - a.request_id);

  return { pedidos: pedidos.slice(0, limite), truncado: pedidos.length > limite };
}

/**
 * Conta os que ESTE usuário pode concluir (mesma regra do botão "Concluir
 * pagamento"). Decisor desconhecido (null/ausente) não conta.
 */
export function contarPagamentosParaConcluir(
  pedidos: ApprovalRequestResponse[],
  contas: Pick<Account, "id" | "status">[],
  decisores: Record<number, number | null>,
  podeDecidir: boolean,
  usuarioId: number | null | undefined,
  agora: Date,
): number {
  let total = 0;
  for (const pedido of pedidos) {
    const conta = contas.find((c) => c.id === pedido.target_account_id);
    if (!conta) continue;
    const situacao = estadoPagamento(conta, [pedido], agora);
    if (podeConcluirPagamento(situacao, podeDecidir, usuarioId, decisores[pedido.request_id])) {
      total += 1;
    }
  }
  return total;
}

function plural(n: number, singular: string, pluralTxt: string): string {
  return n === 1 ? singular : pluralTxt;
}

export interface EntradaAvisos {
  podeVerAprovacoes: boolean;
  podeDecidir: boolean;
  /** null = não carregado/falhou. */
  aprovacoesPendentes: number | null;
  /** null = não carregado/falhou. */
  pagamentos: ContagemConclusao | null;
}

/** Itens do painel; vazio quando não há nada (ou nada confiável). */
export function montarAvisos(entrada: EntradaAvisos): AvisoItem[] {
  const itens: AvisoItem[] = [];

  const pend = entrada.aprovacoesPendentes;
  if (entrada.podeVerAprovacoes && typeof pend === "number" && pend > 0) {
    itens.push({
      chave: "aprovacoes",
      texto: `${pend} ${plural(pend, "aprovação pendente", "aprovações pendentes")}`,
      destino: "/approvals",
    });
  }

  const pag = entrada.pagamentos;
  if (entrada.podeVerAprovacoes && entrada.podeDecidir && pag && pag.total > 0) {
    const base = `${pag.total} ${plural(
      pag.total,
      "pagamento aprovado aguardando",
      "pagamentos aprovados aguardando",
    )} conclusão`;
    itens.push({
      chave: "pagamentos",
      texto: pag.truncado ? `${base} (entre os ${MAX_PEDIDOS_CONFERIDOS} mais recentes)` : base,
      destino: "/recebiveis",
    });
  }

  return itens;
}

/** Contador do sino: soma dos itens exibidos; null quando não há nada. */
export function totalAvisos(itens: AvisoItem[], entrada: EntradaAvisos): number | null {
  let total = 0;
  for (const item of itens) {
    if (item.chave === "aprovacoes") total += entrada.aprovacoesPendentes ?? 0;
    else total += entrada.pagamentos?.total ?? 0;
  }
  return total > 0 ? total : null;
}
