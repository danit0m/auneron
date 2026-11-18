/**
 * B1 -- troca obrigatoria de senha. Logica pura (sem React) para decidir
 * o redirecionamento e validar o formulario.
 */

export const ROTA_TROCAR_SENHA = "/trocar-senha";
export const CODIGO_TROCA_OBRIGATORIA = "password_change_required";
export const TAMANHO_MINIMO_SENHA = 12;
export const TAMANHO_MAXIMO_SENHA = 128;

export const MSG_SENHA_ATUAL_OBRIGATORIA = "Informe a senha atual.";
export const MSG_SENHA_CURTA = `A nova senha precisa ter pelo menos ${TAMANHO_MINIMO_SENHA} caracteres.`;
export const MSG_SENHA_LONGA = `A nova senha pode ter no máximo ${TAMANHO_MAXIMO_SENHA} caracteres.`;
export const MSG_SENHA_IGUAL = "A nova senha precisa ser diferente da senha atual.";
export const MSG_CONFIRMACAO_DIFERENTE = "A confirmação não confere com a nova senha.";

export function precisaTrocarSenha(
  usuario: { must_change_password?: boolean } | null | undefined,
): boolean {
  return Boolean(usuario && usuario.must_change_password === true);
}

/** True para a resposta 403 { detail: { code: "password_change_required" } } da API. */
export function ehErroTrocaObrigatoria(
  status: number | undefined,
  data: unknown,
): boolean {
  if (status !== 403 || !data || typeof data !== "object") return false;
  const detail = (data as { detail?: unknown }).detail;
  return (
    !!detail &&
    typeof detail === "object" &&
    (detail as { code?: unknown }).code === CODIGO_TROCA_OBRIGATORIA
  );
}

/**
 * Para onde ir. Troca pendente: tudo (exceto a propria tela) vai para a tela de troca.
 * Sem pendencia: a tela de troca continua acessivel (troca voluntaria); nenhuma regra extra.
 * Devolve null quando nao ha redirecionamento.
 */
export function destinoDeRedirecionamento(
  autenticado: boolean,
  usuario: { must_change_password?: boolean } | null | undefined,
  caminhoAtual: string,
): string | null {
  if (!autenticado) return null;
  if (precisaTrocarSenha(usuario) && caminhoAtual !== ROTA_TROCAR_SENHA) {
    return ROTA_TROCAR_SENHA;
  }
  return null;
}

/** Destino apos trocar a senha: a rota que o usuario pediu, nunca a propria tela de troca ou o login. */
export function destinoAposTroca(origem: unknown): string {
  if (
    typeof origem === "string" &&
    origem.startsWith("/") &&
    !origem.startsWith("//") &&
    origem !== ROTA_TROCAR_SENHA &&
    origem !== "/login"
  ) {
    return origem;
  }
  return "/";
}

export function validarTrocaDeSenha(
  atual: string,
  nova: string,
  confirmacao: string,
): string | null {
  if (!atual) return MSG_SENHA_ATUAL_OBRIGATORIA;
  if (nova.length < TAMANHO_MINIMO_SENHA) return MSG_SENHA_CURTA;
  if (nova.length > TAMANHO_MAXIMO_SENHA) return MSG_SENHA_LONGA;
  if (nova === atual) return MSG_SENHA_IGUAL;
  if (nova !== confirmacao) return MSG_CONFIRMACAO_DIFERENTE;
  return null;
}
