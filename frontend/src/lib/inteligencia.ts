import type { BotMood } from "../components/ui";
import type {
  ApprovalRequestResponse,
  ApprovalRiskLevel,
} from "../types/approval";
import type { GovernedOperationsSummary } from "../types/dashboard";

export const LIMITE_PARADA_HORAS = 24;
const MS_HORA = 3_600_000;

export type EtapaFunil = "identificadas" | "autonomas" | "humanas" | "verificadas";

export interface EtapaDoFunil {
  chave: EtapaFunil;
  rotulo: string;
  valor: number;
  /** Largura relativa (0 a 100) da barra, proporcional à maior etapa. */
  percentual: number;
}

export interface MetricasAprovacoes {
  /** Média em ms entre criação e decisão (aprovadas/rejeitadas); null sem dados. */
  tempoMedioMs: number | null;
  decididas: number;
  rejeitadas: number;
  /** Pendentes criadas há mais de 24 h. */
  paradas: number;
  pendentes: number;
}

function inteiro(valor: unknown): number | null {
  return typeof valor === "number" && Number.isFinite(valor) && valor >= 0
    ? valor
    : null;
}

function tempo(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : t;
}

/**
 * Funil a partir do resumo de operações governadas. Etapa cujo campo não
 * existe (ou não é número válido) é omitida; nunca se inventa valor.
 */
export function montarFunil(
  resumo: Partial<GovernedOperationsSummary> | null | undefined,
): EtapaDoFunil[] {
  if (!resumo) return [];
  const brutas: Array<{ chave: EtapaFunil; rotulo: string; valor: number | null }> = [
    { chave: "identificadas", rotulo: "Identificadas", valor: inteiro(resumo.eligible_accounts_identified) },
    { chave: "autonomas", rotulo: "Tratadas de forma autônoma", valor: inteiro(resumo.autonomous_dispositions) },
    { chave: "humanas", rotulo: "Encaminhadas para decisão humana", valor: inteiro(resumo.human_governed_dispositions) },
    { chave: "verificadas", rotulo: "Verificadas", valor: inteiro(resumo.autonomous_effect_verification?.verified) },
  ];
  const etapas = brutas.filter(
    (e): e is { chave: EtapaFunil; rotulo: string; valor: number } => e.valor !== null,
  );
  const maior = Math.max(0, ...etapas.map((e) => e.valor));
  return etapas.map((e) => ({
    ...e,
    percentual: maior > 0 ? (e.valor / maior) * 100 : 0,
  }));
}

/**
 * Taxa de acerto = verificadas / (verificadas + checadas com outro resultado).
 * Retorna null quando não há verificadas (o cartão não deve aparecer).
 */
export function calcularTaxaAcerto(
  resumo: Partial<GovernedOperationsSummary> | null | undefined,
): number | null {
  const v = resumo?.autonomous_effect_verification;
  const verificadas = inteiro(v?.verified);
  const outras = inteiro(v?.checked_other);
  if (verificadas === null || verificadas <= 0 || outras === null) return null;
  return verificadas / (verificadas + outras);
}

export function calcularMetricas(
  itens: ReadonlyArray<ApprovalRequestResponse>,
  agora: Date,
): MetricasAprovacoes {
  const limite = LIMITE_PARADA_HORAS * MS_HORA;
  let soma = 0;
  let decididas = 0;
  let rejeitadas = 0;
  let paradas = 0;
  let pendentes = 0;

  for (const item of itens) {
    const criada = tempo(item.created_at);
    if (item.status === "rejected") rejeitadas += 1;
    if (item.status === "approved" || item.status === "rejected") {
      const resolvida = tempo(item.resolved_at);
      if (criada !== null && resolvida !== null && resolvida >= criada) {
        soma += resolvida - criada;
        decididas += 1;
      }
    } else if (item.status === "pending") {
      pendentes += 1;
      if (criada !== null && agora.getTime() - criada > limite) paradas += 1;
    }
  }

  return {
    tempoMedioMs: decididas > 0 ? soma / decididas : null,
    decididas,
    rejeitadas,
    paradas,
    pendentes,
  };
}

/** "45 min", "3 h 20 min", "2 d 4 h". */
export function formatarDuracao(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return "—";
  const minutos = Math.round(ms / 60_000);
  if (minutos < 1) return "menos de 1 min";
  if (minutos < 60) return `${minutos} min`;
  const horas = Math.floor(minutos / 60);
  if (horas < 24) {
    const resto = minutos % 60;
    return resto ? `${horas} h ${resto} min` : `${horas} h`;
  }
  const dias = Math.floor(horas / 24);
  const h = horas % 24;
  return h ? `${dias} d ${h} h` : `${dias} d`;
}

export function formatarTaxa(taxa: number): string {
  return `${Math.round(taxa * 100)}%`;
}

export function humorDaAprovacao(
  risco: ApprovalRiskLevel | undefined,
): BotMood {
  switch (risco) {
    case "critical":
      return "critico";
    case "high":
      return "preocupado";
    case "medium":
      return "atento";
    default:
      return "feliz";
  }
}
