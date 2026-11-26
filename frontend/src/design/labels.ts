/**
 * Rotulos legiveis para a APRESENTACAO. Nunca altere os identificadores
 * tecnicos enviados ou recebidos da API: use estas funcoes somente para
 * exibir texto ao usuario.
 */

function chave(nome: string): string {
  return nome.replace(/[^a-zA-Z0-9]/g, "").toLowerCase();
}

/** Separa CamelCase, snake_case e kebab-case em palavras (primeira letra maiuscula). */
export function humanizarNome(nome: string): string {
  const palavras = nome
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/[_\-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  if (!palavras) return "";
  return palavras.charAt(0).toUpperCase() + palavras.slice(1);
}

const DECISOES: Record<string, string> = {
  criticaloverduedecision: "Análise de inadimplência crítica",
  altovalorematraso: "Cliente estratégico em atraso",
  clienteematraso: "Cliente com pagamento em atraso",
  clientealtovalor: "Cliente de alto valor",
  clienteregular: "Cliente em situação regular",
  baixorisco: "Baixo risco financeiro",
  riscomoderado: "Risco financeiro moderado",
  riscoalto: "Risco financeiro elevado",
  riscocritico: "Risco financeiro crítico",
};

const EVENTOS: Record<string, string> = {
  receivablelifecyclechanged: "Mudança no ciclo de vida da conta a receber",
  clientecriado: "Cliente cadastrado",
  contavencida: "Conta vencida",
  accountstatechanged: "Mudança na situação da conta",
  customercreated: "Cliente cadastrado",
};

const AGENTES: Record<string, string> = {
  receivablesmonitoragent: "Monitor de contas a receber",
  overduedetectionagent: "Detecção de inadimplência",
  riskagent: "Análise de risco",
  financeagent: "Análise financeira",
  analyticsagent: "Análise de indicadores",
  notificationagent: "Notificações",
};

const SINAIS: Record<string, string> = {
  diasatraso: "Dias em atraso",
  statusatrasado: "Situação: atrasado",
  statuspago: "Situação: pago",
  valoralto: "Valor alto",
  clienteestrategico: "Cliente estratégico",
  clientepremium: "Cliente premium",
  riskscore: "Pontuação de risco",
  contavencida: "Conta vencida",
};

function buscar(mapa: Record<string, string>, nome: string): string {
  return mapa[chave(nome)] ?? humanizarNome(nome);
}

export function rotuloDecisao(nome: string): string {
  return buscar(DECISOES, nome);
}

export function rotuloEvento(nome: string): string {
  return buscar(EVENTOS, nome);
}

export function rotuloAgente(nome: string): string {
  const conhecido = AGENTES[chave(nome)];
  if (conhecido) return conhecido;
  // Fallback: remove o sufixo tecnico "Agent" e separa as palavras.
  const semSufixo = nome.replace(/[_\s-]*agent$/i, "");
  return humanizarNome(semSufixo || nome);
}

export function rotuloSinal(nome: string): string {
  return buscar(SINAIS, nome);
}

/** Termos de interface em ingles que aparecem para o usuario. */
export const TERMOS = {
  confidenceScore: "Nível de confiança",
  workItem: "Tarefa operacional",
  reasoning: "Fundamentação da análise",
  pendingApproval: "Aguardando aprovação",
} as const;
