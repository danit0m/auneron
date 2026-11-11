import type { BotMood, InvoiceSituation } from "./AuneronBot.types";

export const MOOD_LABEL: Record<BotMood, string> = {
  feliz: "Contente: tudo em dia",
  celebrando: "Comemorando: pagamento recebido",
  atento: "Atento: vencimento próximo",
  pensando: "Analisando",
  preocupado: "Preocupado: fatura em atraso",
  critico: "Alerta: atraso crítico",
  dormindo: "Em repouso: nada pendente",
};

/** Traduz a situação de uma fatura no humor do assistente. */
export function moodFromInvoice(s: InvoiceSituation): BotMood {
  if (s.status === "processando") return "pensando";
  if (s.status === "sem_pendencias") return "dormindo";
  if (s.status === "paga") return s.pagaAgora ? "celebrando" : "feliz";
  const dias = s.diasParaVencer;
  if (dias === undefined || !Number.isFinite(dias)) return "pensando";
  if (dias < -30) return "critico";
  if (dias < 0) return "preocupado";
  if (dias <= 3) return "atento";
  return "feliz";
}
