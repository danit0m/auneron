import type { ApprovalRequestResponse } from "../types/approval";

type PedidoParaDecisao = Pick<
  ApprovalRequestResponse,
  "status" | "requester_actor_type" | "requester_user_id"
>;

/**
 * Params da listagem. O backend le `status=pending` (valor unico); uma lista
 * viraria `status[]=pending` no axios e seria ignorada, devolvendo tudo.
 */
export function paramsListaAprovacoes(statusFiltro: string): {
  status?: string;
  limit: number;
} {
  return statusFiltro === "todos"
    ? { limit: 100 }
    : { status: statusFiltro, limit: 100 };
}

/** True quando o pedido foi feito pelo proprio usuario logado (comparacao por id). */
export function usuarioEhSolicitante(
  item: PedidoParaDecisao,
  usuarioId: number | null | undefined,
): boolean {
  return (
    usuarioId != null &&
    item.requester_actor_type === "user" &&
    item.requester_user_id != null &&
    item.requester_user_id === usuarioId
  );
}

/**
 * Decide se a tela oferece Aprovar/Rejeitar. O backend continua sendo a
 * autoridade (solicitante != decisor); isto so evita oferecer o que sera recusado.
 */
export function podeDecidirPedido(
  item: PedidoParaDecisao,
  usuarioId: number | null | undefined,
  podeDecidirPermissao: boolean,
): boolean {
  if (!podeDecidirPermissao) return false;
  if (item.status !== "pending") return false;
  if (usuarioId == null) return false;
  return !usuarioEhSolicitante(item, usuarioId);
}

/**
 * Rotulo do status na tela de Aprovacoes. Aprovado e ja executado mostra a data
 * da execucao (dia civil de Brasilia); aprovado sem consumo segue "Aprovada".
 */
export function rotuloStatusPedido(
  item: Pick<ApprovalRequestResponse, "status" | "consumed_at">,
): string {
  const base = {
    pending: "Pendente",
    approved: "Aprovada",
    rejected: "Rejeitada",
    expired: "Expirada",
    cancelled: "Cancelada",
  }[item.status];
  if (item.status !== "approved" || !item.consumed_at) return base;
  const momento = new Date(item.consumed_at);
  if (Number.isNaN(momento.getTime())) return base;
  const [ano, mes, dia] = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Sao_Paulo",
  })
    .format(momento)
    .split("-");
  return `${base} · executada em ${dia}/${mes}/${ano}`;
}
