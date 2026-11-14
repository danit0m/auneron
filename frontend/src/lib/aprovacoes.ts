import type { ApprovalRequestResponse } from "../types/approval";

type PedidoParaDecisao = Pick<
  ApprovalRequestResponse,
  "status" | "requester_actor_type" | "requester_user_id"
>;

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
