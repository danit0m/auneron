import {
  AlertTriangle,
  BadgeCheck,
  CalendarClock,
  Info,
} from "lucide-react";

import type {
  LatestDecisionResponse,
} from "../../types/orchestrator";
import { rotuloDecisao, rotuloSinal } from "../../design/labels";
import { montarExplicabilidade } from "./explicabilidade";

import "./styles/ExplainabilityCard.css";

interface Props {
  data: LatestDecisionResponse | null;
}

// Mostra apenas o que a analise registrou (sinais, dados do titulo, justificativa e confianca).
// Nada e presumido sobre o cliente; o que nao veio nos dados e omitido ou sinalizado.
export default function ExplainabilityCard({
  data,
}: Props) {
  if (
    !data?.available ||
    !data.decision
  ) {
    return (
      <section className="explainability-card">
        <h3>Explicabilidade da análise</h3>

        <div className="explainability-empty">
          Nenhuma decisão disponível.
        </div>
      </section>
    );
  }

  const d = data.decision;
  const x = montarExplicabilidade(d, rotuloSinal);

  return (
    <section className="explainability-card">
      <div className="section-title">
        <BadgeCheck size={20} aria-hidden="true" />

        <div>
          <h3>Explicabilidade da análise</h3>
          <span>
            O que a análise registrou para
            {" "}
            {rotuloDecisao(d.decision_name)}
          </span>
        </div>
      </div>

      {x.semDados ? (
        <div className="explainability-empty">
          <Info size={16} aria-hidden="true" />
          {" "}
          A análise não registrou fatores detalhados para este caso.
        </div>
      ) : (
        <div className="explainability-list">
          {x.fatores.map((f) => (
            <article key={f.chave} className="explainability-item">
              <div className="explainability-header">
                <div>
                  <strong>{f.titulo}</strong>
                  <span>{f.valor}</span>
                </div>
              </div>

              {f.descricao && <p>{f.descricao}</p>}
            </article>
          ))}
        </div>
      )}

      <div className="executive-summary">
        <Info size={18} aria-hidden="true" />

        <div>
          <strong>Resumo da análise</strong>

          <span>
            {x.justificativa ?? "A análise não registrou uma justificativa em texto."}
            {x.confianca && (
              <>
                {" "}
                Nível de confiança registrado:
                {" "}
                <b>{x.confianca}</b>.
              </>
            )}
          </span>
        </div>
      </div>

      {x.marcadores.length > 0 && (
        <div className="decision-flags">
          {x.marcadores.map((m, i) => (
            <div key={m}>
              {i === 0 ? (
                <AlertTriangle size={17} aria-hidden="true" />
              ) : (
                <CalendarClock size={17} aria-hidden="true" />
              )}
              {m}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
