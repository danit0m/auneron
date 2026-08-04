import {
  BadgeCheck,
  Bot,
  Gauge,
  Layers3,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import type {
  LatestDecisionResponse,
} from "../../types/orchestrator";

interface ConfidenceMeterProps {
  data: LatestDecisionResponse | null;
}

interface ConfidenceProfile {
  label: string;
  assessment: string;
  className: string;
}

function getConfidenceProfile(
  percentage: number,
): ConfidenceProfile {
  if (percentage >= 95) {
    return {
      label: "Muito alta",
      assessment:
        "A IA apresenta elevada confiabilidade para esta decisão, com forte sustentação nas regras e evidências analisadas.",
      className:
        "confidence-meter-excellent",
    };
  }

  if (percentage >= 85) {
    return {
      label: "Alta",
      assessment:
        "A decisão possui alta consistência e baixo risco de divergência, mantendo boa sustentação nas evidências.",
      className:
        "confidence-meter-high",
    };
  }

  if (percentage >= 70) {
    return {
      label: "Moderada",
      assessment:
        "A decisão é consistente, mas recomenda-se acompanhamento executivo antes de ações mais sensíveis.",
      className:
        "confidence-meter-medium",
    };
  }

  return {
    label: "Baixa",
    assessment:
      "A decisão apresenta baixa sustentação e deve ser revisada antes da execução ou aprovação.",
    className:
      "confidence-meter-low",
  };
}

function formatDecisionName(
  value: string,
): string {
  return value
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(
      /\b\w/g,
      (letter) => letter.toUpperCase(),
    );
}

export default function ConfidenceMeter({
  data,
}: ConfidenceMeterProps) {
  if (
    !data?.available ||
    !data.decision
  ) {
    return (
      <article className="confidence-meter-card">
        <div className="confidence-meter-header">
          <div className="confidence-meter-title">
            <div className="confidence-meter-title-icon">
              <Gauge size={22} />
            </div>

            <div>
              <span>
                AI Confidence Intelligence
              </span>

              <h3>
                Confiança ainda não calculada
              </h3>
            </div>
          </div>
        </div>

        <div className="confidence-meter-empty">
          <Gauge size={38} />

          <strong>
            Nenhuma avaliação disponível
          </strong>

          <p>
            O indicador será preenchido
            quando o Decision Engine gerar
            uma nova decisão.
          </p>
        </div>
      </article>
    );
  }

  const decision = data.decision;

  const percentage = Math.min(
    Math.max(
      decision.confidence_percentage,
      0,
    ),
    100,
  );

  const profile =
    getConfidenceProfile(percentage);

  return (
    <article
      className={`confidence-meter-card ${profile.className}`}
    >
      <div className="confidence-meter-header">
        <div className="confidence-meter-title">
          <div className="confidence-meter-title-icon">
            <Gauge size={22} />
          </div>

          <div>
            <span>
              AI Confidence Intelligence
            </span>

            <h3>
              Confiança da decisão
            </h3>
          </div>
        </div>

        <span className="confidence-meter-decision">
          {formatDecisionName(
            decision.decision_name,
          )}
        </span>
      </div>

      <div className="confidence-meter-main">
        <div className="confidence-meter-score">
          <span className="confidence-meter-number">
            {percentage.toFixed(1)}
          </span>

          <span className="confidence-meter-percent">
            %
          </span>

          <span className="confidence-meter-label">
            {profile.label}
          </span>
        </div>

        <div className="confidence-meter-visual">
          <div className="confidence-meter-progress-header">
            <span>
              Escala de confiança
            </span>

            <strong>
              {profile.label}
            </strong>
          </div>

          <div className="confidence-meter-track">
            <div
              className="confidence-meter-value"
              style={{
                width: `${percentage}%`,
              }}
            />
          </div>

          <div className="confidence-meter-scale">
            <span>Baixa</span>
            <span>Moderada</span>
            <span>Alta</span>
            <span>Muito alta</span>
          </div>
        </div>
      </div>

      <div className="confidence-meter-assessment">
        <Sparkles size={18} />

        <div>
          <span>
            Executive Assessment
          </span>

          <p>
            {profile.assessment}
          </p>
        </div>
      </div>

      <div className="confidence-meter-evidence-grid">
        <div className="confidence-meter-evidence-item">
          <div className="confidence-meter-evidence-icon">
            <BadgeCheck size={17} />
          </div>

          <div>
            <strong>
              {
                decision.signals.length
              }
            </strong>

            <span>
              evidências identificadas
            </span>
          </div>
        </div>

        <div className="confidence-meter-evidence-item">
          <div className="confidence-meter-evidence-icon">
            <Bot size={17} />
          </div>

          <div>
            <strong>
              {
                decision.selected_agents
                  .length
              }
            </strong>

            <span>
              agentes especializados
            </span>
          </div>
        </div>

        <div className="confidence-meter-evidence-item">
          <div className="confidence-meter-evidence-icon">
            <Layers3 size={17} />
          </div>

          <div>
            <strong>
              Regra aplicada
            </strong>

            <span>
              {formatDecisionName(
                decision.decision_name,
              )}
            </span>
          </div>
        </div>

        <div className="confidence-meter-evidence-item">
          <div className="confidence-meter-evidence-icon">
            <ShieldCheck size={17} />
          </div>

          <div>
            <strong>
              Contexto consistente
            </strong>

            <span>
              dados validados pelo motor
            </span>
          </div>
        </div>
      </div>
    </article>
  );
}