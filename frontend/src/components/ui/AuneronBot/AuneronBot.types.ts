import type { HTMLAttributes } from "react";

export type BotMood =
  | "feliz"
  | "celebrando"
  | "atento"
  | "pensando"
  | "preocupado"
  | "critico"
  | "dormindo";

export type BotSize = "sm" | "md" | "lg";

export interface AuneronBotProps extends HTMLAttributes<HTMLDivElement> {
  /** Humor do assistente; use moodFromInvoice quando o contexto for uma fatura. */
  mood?: BotMood;
  size?: BotSize;
  /** Fala exibida num balão ao lado do robô. */
  message?: string;
  /** Texto acessível; o padrão descreve o humor. */
  label?: string;
}

export interface InvoiceSituation {
  status: "paga" | "aberta" | "processando" | "sem_pendencias";
  /** Dias até o vencimento (positivo) ou de atraso (negativo). */
  diasParaVencer?: number;
  /** Pago nas últimas 24h: comemora. */
  pagaAgora?: boolean;
}
