import {
  AlertTriangle,
  BadgeCheck,
  CalendarClock,
  CheckCircle2,
  CircleDollarSign,
} from "lucide-react";

import type {
  LatestDecisionResponse,
} from "../../types/orchestrator";

interface Props {
  data: LatestDecisionResponse | null;
}

interface ExplainabilityItem {
  title: string;
  value: string;
  impact: "Alto" | "Muito Alto" | "Moderado";
  description: string;
  percentage: number;
}

function currency(value: number) {
  return new Intl.NumberFormat(
    "pt-BR",
    {
      style: "currency",
      currency: "BRL",
    },
  ).format(value);
}

export default function ExplainabilityCard({
  data,
}: Props) {
  if (
    !data?.available ||
    !data.decision
  ) {
    return (
      <section className="explainability-card">
        <h3>Explainability Engine</h3>

        <div className="explainability-empty">
          Nenhuma decisão disponível.
        </div>
      </section>
    );
  }

  const d = data.decision;

  const items: ExplainabilityItem[] = [
    {
      title: "Valor Financeiro",
      value: currency(
        d.context.valor,
      ),
      impact:
        d.context.valor >= 30000
          ? "Muito Alto"
          : "Moderado",
      description:
        "O valor financeiro posiciona este cliente como estratégico para a empresa.",
      percentage:
        d.context.valor >= 30000
          ? 100
          : 60,
    },
    {
      title: "Status Financeiro",
      value:
        d.context.status.toUpperCase(),
      impact:
        d.context.status ===
        "atrasado"
          ? "Alto"
          : "Moderado",
      description:
        d.context.status ===
        "atrasado"
          ? "Foi identificada inadimplência ativa."
          : "Cliente encontra-se em situação regular.",
      percentage:
        d.context.status ===
        "atrasado"
          ? 90
          : 50,
    },
    {
      title: "Dias em atraso",
      value: `${d.context.dias_atraso} dias`,
      impact:
        d.context.dias_atraso >= 3
          ? "Alto"
          : "Moderado",
      description:
        "O tempo de atraso aumenta o risco financeiro.",
      percentage:
        d.context.dias_atraso >= 3
          ? 85
          : 45,
    },
  ];

  return (
    <section className="explainability-card">

      <div className="section-title">

        <BadgeCheck size={20} />

        <div>

          <h3>Explainability Engine</h3>

          <span>
            Como a IA chegou
            nesta decisão
          </span>

        </div>

      </div>

      <div className="explainability-list">

        {items.map((item) => (

          <article
            key={item.title}
            className="explainability-item"
          >

            <div className="explainability-header">

              <div>

                <strong>
                  {item.title}
                </strong>

                <span>
                  {item.value}
                </span>

              </div>

              <span className="impact-badge">
                {item.impact}
              </span>

            </div>

            <div className="impact-bar">

              <div
                className="impact-value"
                style={{
                  width:
                    `${item.percentage}%`,
                }}
              />

            </div>

            <p>
              {item.description}
            </p>

          </article>

        ))}

      </div>

      <div className="executive-summary">

        <CheckCircle2 size={18} />

        <div>

          <strong>
            Executive Summary
          </strong>

          <span>

            A IA identificou um conjunto
            consistente de evidências
            financeiras que justificam
            a aplicação da estratégia

            <b>
              {" "}
              {d.decision_name}
            </b>

            .

          </span>

        </div>

      </div>

      <div className="decision-flags">

        <div>

          <CircleDollarSign
            size={17}
          />

          Cliente estratégico

        </div>

        <div>

          <AlertTriangle
            size={17}
          />

          Pagamento atrasado

        </div>

        <div>

          <CalendarClock
            size={17}
          />

          {d.context.dias_atraso}
          {" "}
          dias em atraso

        </div>

      </div>

    </section>
  );
}