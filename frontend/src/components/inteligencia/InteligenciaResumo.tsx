import { useEffect, useMemo, useState } from "react";

import api from "../../api/api";
import {
  calcularMetricas,
  calcularTaxaAcerto,
  formatarDuracao,
  formatarTaxa,
  montarFunil,
} from "../../lib/inteligencia";
import type { ApprovalListResponse } from "../../types/approval";
import type { GovernedOperationsSummary } from "../../types/dashboard";
import { AuneronBot } from "../ui";
import type { BotMood } from "../ui";
import "./InteligenciaResumo.css";

interface Props {
  /** Muda quando a fila é recarregada; dispara nova leitura (somente leitura). */
  gatilho: unknown;
}

interface Cartao {
  chave: string;
  titulo: string;
  valor: string;
  detalhe: string;
  mood: BotMood;
}

export default function InteligenciaResumo({ gatilho }: Props) {
  const [resumo, setResumo] = useState<GovernedOperationsSummary | null>(null);
  const [aprovacoes, setAprovacoes] = useState<
    ApprovalListResponse["items"] | null
  >(null);

  useEffect(() => {
    let ativo = true;

    api
      .get<GovernedOperationsSummary>("/dashboard/governed-operations-summary")
      .then((r) => ativo && setResumo(r.data))
      .catch(() => ativo && setResumo(null));

    api
      .get<ApprovalListResponse>("/approvals", { params: { limit: 100 } })
      .then((r) => ativo && setAprovacoes(r.data.items))
      .catch(() => ativo && setAprovacoes(null));

    return () => {
      ativo = false;
    };
  }, [gatilho]);

  const funil = useMemo(() => montarFunil(resumo), [resumo]);
  const taxa = useMemo(() => calcularTaxaAcerto(resumo), [resumo]);
  const metricas = useMemo(
    () => (aprovacoes ? calcularMetricas(aprovacoes, new Date()) : null),
    [aprovacoes],
  );

  const cartoes: Cartao[] = [];

  if (metricas) {
    if (metricas.tempoMedioMs !== null) {
      cartoes.push({
        chave: "tempo",
        titulo: "Tempo médio até decidir",
        valor: formatarDuracao(metricas.tempoMedioMs),
        detalhe: `${metricas.decididas} decididas (últimas 100 solicitações)`,
        mood: "pensando",
      });
    }
    cartoes.push({
      chave: "rejeitadas",
      titulo: "Rejeitadas",
      valor: String(metricas.rejeitadas),
      detalhe: "Últimas 100 solicitações",
      mood: "atento",
    });
    cartoes.push({
      chave: "paradas",
      titulo: "Paradas há mais de 24 h",
      valor: String(metricas.paradas),
      detalhe: `${metricas.pendentes} pendentes no total`,
      mood: metricas.paradas > 0 ? "preocupado" : "feliz",
    });
  }

  if (taxa !== null) {
    cartoes.push({
      chave: "acerto",
      titulo: "Taxa de acerto",
      valor: formatarTaxa(taxa),
      detalhe: "Efeitos autônomos verificados entre os checados",
      mood: "celebrando",
    });
  }

  if (funil.length === 0 && cartoes.length === 0) return null;

  return (
    <div className="inteligencia-resumo">
      {funil.length > 0 && (
        <section className="inteligencia-funil" aria-label="Do que a IA viu ao que resolveu">
          <h2>Do que a IA viu ao que resolveu</h2>
          <p className="inteligencia-sub">
            Contas identificadas, tratadas e verificadas no período do resumo.
          </p>
          <ol className="inteligencia-etapas">
            {funil.map((etapa) => (
              <li key={etapa.chave} className={`inteligencia-etapa inteligencia-etapa-${etapa.chave}`}>
                <span className="inteligencia-etapa-rotulo">{etapa.rotulo}</span>
                <span className="inteligencia-barra" aria-hidden="true">
                  <span style={{ width: `${etapa.percentual}%` }} />
                </span>
                <strong>{etapa.valor}</strong>
              </li>
            ))}
          </ol>
        </section>
      )}

      {cartoes.length > 0 && (
        <div className="inteligencia-cartoes">
          {cartoes.map((c) => (
            <article key={c.chave} className="inteligencia-cartao">
              <AuneronBot mood={c.mood} size="sm" />
              <div className="inteligencia-cartao-texto">
                <span>{c.titulo}</span>
                <strong>{c.valor}</strong>
                <small>{c.detalhe}</small>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
