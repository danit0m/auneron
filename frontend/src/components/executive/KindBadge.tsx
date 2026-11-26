import { CheckCircle2, Lightbulb, Search } from "lucide-react";

import "./styles/KindBadge.css";

export type KindBadgeKind = "analise" | "recomendacao" | "acao-aprovada";

const KINDS = {
  analise: { label: "Análise", Icon: Search, help: "Leitura dos dados registrados; não executa nada." },
  recomendacao: { label: "Recomendação", Icon: Lightbulb, help: "Sugestão de próximo passo; ainda não foi executada." },
  "acao-aprovada": { label: "Ação aprovada", Icon: CheckCircle2, help: "Passo que uma pessoa autorizada aprovou." },
} as const;

/**
 * Selo que diferencia analise, recomendacao e acao aprovada por texto e icone
 * (nao apenas por cor). Somente apresentacao.
 */
export function KindBadge({ kind }: { kind: KindBadgeKind }) {
  const { label, Icon, help } = KINDS[kind];
  return (
    <b className={`kind-badge kind-badge-${kind}`} title={help}>
      <Icon size={14} aria-hidden="true" />
      {label}
    </b>
  );
}

export default KindBadge;
