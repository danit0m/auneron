import type { Account } from "../types/account";

/**
 * Lógica pura do "Completar contato" por cliente. A regra de WhatsApp
 * espelha a do backend (schemas/account.py: normalize_whatsapp).
 */

export const WHATSAPP_MIN_DIGITOS = 10;
export const WHATSAPP_MAX_DIGITOS = 15;

export const MSG_WHATSAPP_INVALIDO =
  "WhatsApp inválido: use apenas números, com + opcional no início, de 10 a 15 dígitos.";
export const MSG_EMAIL_INVALIDO = "Informe um e-mail válido.";
export const MSG_SEM_CONTATO =
  "Informe ao menos um contato: e-mail ou WhatsApp.";

/** Mesma regra de e-mail do ClienteModal. */
const REGEX_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const REGEX_WHATSAPP = new RegExp(
  `^\\+?[0-9]{${WHATSAPP_MIN_DIGITOS},${WHATSAPP_MAX_DIGITOS}}$`,
);

/**
 * Remove espaços, hífens e parênteses; devolve o número normalizado
 * (com o + inicial, se veio) ou null se inválido. Vazio devolve null.
 */
export function normalizarWhatsapp(valor: string | null | undefined): string | null {
  const limpo = (valor ?? "").replace(/[\s\-()]/g, "");
  return REGEX_WHATSAPP.test(limpo) ? limpo : null;
}

export function emailValido(valor: string): boolean {
  return REGEX_EMAIL.test(valor.trim());
}

function preenchido(valor: string | null | undefined): valor is string {
  return typeof valor === "string" && valor.trim() !== "";
}

export interface FormularioContato {
  email: string;
  whatsapp: string;
}

export interface ErrosContato {
  email?: string;
  whatsapp?: string;
  geral?: string;
}

export interface ContatoValidado {
  /** null = não enviar (não altera o campo no servidor). */
  email: string | null;
  whatsapp: string | null;
  erros: ErrosContato;
  valido: boolean;
}

/** Valida o formulário e devolve os valores prontos para envio. */
export function validarContato(form: FormularioContato): ContatoValidado {
  const erros: ErrosContato = {};
  const emailTexto = form.email.trim();
  const whatsappTexto = form.whatsapp.trim();

  let email: string | null = null;
  let whatsapp: string | null = null;

  if (emailTexto) {
    if (emailValido(emailTexto)) email = emailTexto;
    else erros.email = MSG_EMAIL_INVALIDO;
  }

  if (whatsappTexto) {
    whatsapp = normalizarWhatsapp(whatsappTexto);
    if (whatsapp === null) erros.whatsapp = MSG_WHATSAPP_INVALIDO;
  }

  if (!emailTexto && !whatsappTexto) erros.geral = MSG_SEM_CONTATO;

  return {
    email,
    whatsapp,
    erros,
    valido: Object.keys(erros).length === 0,
  };
}

/** Mesmo critério do backend: lower(trim(cliente)). */
export function chaveCliente(nome: string): string {
  return nome.trim().toLowerCase();
}

/** Títulos do mesmo cliente (nome sem espaços nas pontas, sem diferenciar caixa). */
export function titulosDoCliente(contas: Account[], nome: string): Account[] {
  const chave = chaveCliente(nome);
  return contas.filter((c) => chaveCliente(c.cliente) === chave);
}

export interface ContatosDistintos {
  emails: string[];
  whatsapps: string[];
  /** Mais de um e-mail ou mais de um WhatsApp entre os títulos. */
  divergente: boolean;
}

/** Valores de contato distintos (na ordem em que aparecem) entre os títulos. */
export function contatosDistintos(titulos: Account[]): ContatosDistintos {
  const emails: string[] = [];
  const whatsapps: string[] = [];

  for (const t of titulos) {
    if (preenchido(t.email)) {
      const v = t.email.trim();
      if (!emails.includes(v)) emails.push(v);
    }
    if (preenchido(t.whatsapp)) {
      const v = t.whatsapp.trim();
      if (!whatsapps.includes(v)) whatsapps.push(v);
    }
  }

  return {
    emails,
    whatsapps,
    divergente: emails.length > 1 || whatsapps.length > 1,
  };
}

/** Valor inicial do formulário: o contato único encontrado, ou vazio se não há/diverge. */
export function formularioInicial(titulos: Account[]): FormularioContato {
  const { emails, whatsapps } = contatosDistintos(titulos);
  return {
    email: emails.length === 1 ? emails[0] : "",
    whatsapp: whatsapps.length === 1 ? whatsapps[0] : "",
  };
}
