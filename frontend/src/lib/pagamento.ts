import type { Account } from "../types/account";
import type { ApprovalRequestResponse } from "../types/approval";

/**
 * Lógica pura do "Registrar pagamento" (A2/A3). A validação definitiva é
 * do backend (data não futura no "hoje de negócio"); aqui só se antecipa
 * o erro para o operador, usando o dia local do navegador.
 */

export const SKILL_MARK_PAID = "account.mark_paid";

export const MSG_DATA_OBRIGATORIA = "Informe a data do pagamento.";
export const MSG_DATA_INVALIDA = "Data inválida. Use o formato dia/mês/ano.";
export const MSG_DATA_FUTURA = "A data do pagamento não pode ser futura.";

export type EstadoPagamento =
  | "sem-pedido"
  | "aguardando-aprovacao"
  | "aprovado-aguardando-conclusao"
  | "pago";

export interface SituacaoPagamento {
  estado: EstadoPagamento;
  /** Pedido que define o estado (pendente ou aprovado); ausente nos demais. */
  pedido: ApprovalRequestResponse | null;
}

export interface ResultadoDataPagamento {
  valida: boolean;
  erro: string;
}

const REGEX_ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

function doisDigitos(n: number): string {
  return String(n).padStart(2, "0");
}

/** AAAA-MM-DD do dia local de `data`. */
export function dataIsoLocal(data: Date): string {
  return `${data.getFullYear()}-${doisDigitos(data.getMonth() + 1)}-${doisDigitos(data.getDate())}`;
}

/** true somente para datas de calendário reais (rejeita 2026-02-30). */
export function dataIsoValida(valor: string): boolean {
  const partes = REGEX_ISO.exec(valor);
  if (!partes) return false;

  const ano = Number(partes[1]);
  const mes = Number(partes[2]);
  const dia = Number(partes[3]);
  const data = new Date(ano, mes - 1, dia);

  return (
    data.getFullYear() === ano &&
    data.getMonth() === mes - 1 &&
    data.getDate() === dia
  );
}

/** dd/mm/aaaa a partir de AAAA-MM-DD; "—" quando ausente ou inválido. */
export function formatarDataBr(valor: string | null | undefined): string {
  if (!valor || !dataIsoValida(valor)) return "—";
  const [ano, mes, dia] = valor.split("-");
  return `${dia}/${mes}/${ano}`;
}

/** Data obrigatória, válida e não futura (comparação de AAAA-MM-DD). */
export function validarDataPagamento(
  valor: string,
  hoje: Date,
): ResultadoDataPagamento {
  const texto = (valor ?? "").trim();

  if (texto === "") return { valida: false, erro: MSG_DATA_OBRIGATORIA };
  if (!dataIsoValida(texto)) return { valida: false, erro: MSG_DATA_INVALIDA };
  if (texto > dataIsoLocal(hoje)) return { valida: false, erro: MSG_DATA_FUTURA };

  return { valida: true, erro: "" };
}

/**
 * Aviso (não bloqueia) quando a data do pagamento é anterior ao dia de
 * cadastro do título. Devolve null quando não há o que avisar.
 */
export function avisoDataAnteriorAoCadastro(
  valor: string,
  criadoEm: string | null | undefined,
): string | null {
  if (!dataIsoValida(valor) || !criadoEm) return null;

  const criacao = new Date(criadoEm);
  if (Number.isNaN(criacao.getTime())) return null;

  const diaCadastro = dataIsoLocal(criacao);
  if (valor >= diaCadastro) return null;

  return (
    `A data informada é anterior ao cadastro do título (${formatarDataBr(diaCadastro)}). ` +
    "Confirme se está correta; o pedido segue para aprovação mesmo assim."
  );
}

function aindaValido(pedido: ApprovalRequestResponse, agora: Date): boolean {
  const expira = new Date(pedido.expires_at).getTime();
  return Number.isNaN(expira) ? false : expira > agora.getTime();
}

function maisRecente(
  pedidos: ApprovalRequestResponse[],
): ApprovalRequestResponse | null {
  let escolhido: ApprovalRequestResponse | null = null;
  for (const pedido of pedidos) {
    if (!escolhido || pedido.request_id > escolhido.request_id) escolhido = pedido;
  }
  return escolhido;
}

/**
 * Estado do título quanto ao pagamento, a partir da conta e da lista de
 * aprovações. Só pedidos account.mark_paid da própria conta e ainda não
 * expirados contam; aprovado vale mais que pendente.
 */
export function estadoPagamento(
  conta: Pick<Account, "id" | "status">,
  aprovacoes: ApprovalRequestResponse[],
  agora: Date,
): SituacaoPagamento {
  if (conta.status === "pago") return { estado: "pago", pedido: null };

  const doTitulo = aprovacoes.filter(
    (a) =>
      a.skill_key === SKILL_MARK_PAID &&
      a.target_account_id === conta.id &&
      aindaValido(a, agora),
  );

  const aprovado = maisRecente(doTitulo.filter((a) => a.status === "approved"));
  if (aprovado) {
    return { estado: "aprovado-aguardando-conclusao", pedido: aprovado };
  }

  const pendente = maisRecente(doTitulo.filter((a) => a.status === "pending"));
  if (pendente) return { estado: "aguardando-aprovacao", pedido: pendente };

  return { estado: "sem-pedido", pedido: null };
}

/** Rótulo curto do estado; vazio para "sem-pedido". */
export function rotuloEstadoPagamento(situacao: SituacaoPagamento): string {
  switch (situacao.estado) {
    case "aguardando-aprovacao":
      return "Aguardando aprovação";
    case "aprovado-aguardando-conclusao":
      return "Aprovado — falta concluir";
    case "pago":
      return "Pago";
    default:
      return "";
  }
}

/** Conclusão manual: aprovado, com permissão e usuário diferente do decisor. */
export function podeConcluirPagamento(
  situacao: SituacaoPagamento,
  podeDecidir: boolean,
  usuarioId: number | null | undefined,
  decisorId: number | null | undefined,
): boolean {
  return (
    situacao.estado === "aprovado-aguardando-conclusao" &&
    podeDecidir &&
    typeof usuarioId === "number" &&
    typeof decisorId === "number" &&
    usuarioId !== decisorId
  );
}

/** "Pago em dd/mm/aaaa" quando há data efetiva registrada; senão null. */
export function descreverPagamento(
  conta: Pick<Account, "status" | "paid_on">,
): string | null {
  if (conta.status !== "pago") return null;
  if (!conta.paid_on || !dataIsoValida(conta.paid_on)) return null;
  return `Pago em ${formatarDataBr(conta.paid_on)}`;
}
