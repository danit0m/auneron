import {
  AlertTriangle,
  BadgeCheck,
  Bot,
  ShieldCheck,
  UserCheck,
} from "lucide-react";
import { useEffect, useState } from "react";

import api, { getApiErrorMessage } from "../../api/api";
import type { GovernedOperationsSummary as GovernedOperationsSummaryData } from "../../types/dashboard";

import "./styles/GovernedOperationsSummary.css";

function formatarPercentual(valor: number | null): string {
  if (valor === null) {
    return "—";
  }

  return `${(valor * 100).toFixed(1)}%`;
}

function formatarData(iso: string): string {
  const data = new Date(iso);

  if (Number.isNaN(data.getTime())) {
    return iso;
  }

  return data.toLocaleDateString("pt-BR");
}

export function GovernedOperationsSummary() {
  const [dados, setDados] =
    useState<GovernedOperationsSummaryData | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState("");

  useEffect(() => {
    async function carregar() {
      try {
        setCarregando(true);
        setErro("");

        const response = await api.get<GovernedOperationsSummaryData>(
          "/dashboard/governed-operations-summary",
        );

        setDados(response.data);
      } catch (error) {
        console.error(
          "Erro ao carregar o resumo de operações governadas:",
          error,
        );

        setErro(
          getApiErrorMessage(
            error,
            "Não foi possível carregar o resumo de operações governadas.",
          ),
        );
      } finally {
        setCarregando(false);
      }
    }

    void carregar();
  }, []);

  if (carregando) {
    return (
      <section className="governed-summary">
        <div className="governed-summary-state">
          <div className="loading-spinner" />
          <p>Carregando operações governadas...</p>
        </div>
      </section>
    );
  }

  if (erro || !dados) {
    return (
      <section className="governed-summary">
        <div className="governed-summary-state governed-summary-state-error">
          <AlertTriangle size={22} />
          <span>
            {erro ||
              "Não foi possível carregar o resumo de operações governadas."}
          </span>
        </div>
      </section>
    );
  }

  const {
    period,
    eligible_accounts_identified,
    autonomous_dispositions,
    human_governed_dispositions,
    autonomous_disposition_rate,
    autonomous_effect_verification,
    pending_overdue_accounts_now,
  } = dados;

  return (
    <section className="governed-summary">
      <div className="governed-summary-header">
        <div>
          <span className="governed-summary-eyebrow">
            Governança operacional
          </span>
          <h2>Operações governadas</h2>
          <p>
            O que o Auneron identificou, tratou e verificou sob
            governança nos últimos {period.days} dias.
          </p>
        </div>
      </div>

      <div className="governed-summary-block">
        <div className="governed-summary-block-header">
          <span>Período</span>
          <strong>
            {formatarData(period.start)} — {formatarData(period.end)}
          </strong>
        </div>

        <div className="governed-summary-grid">
          <article className="governed-summary-card">
            <div className="governed-summary-icon governed-summary-icon-slate">
              <BadgeCheck size={20} />
            </div>
            <div className="governed-summary-card-content">
              <span>Contas identificadas</span>
              <strong>{eligible_accounts_identified}</strong>
              <small>Venceram no período sem pagamento até a data</small>
            </div>
          </article>

          <article className="governed-summary-card">
            <div className="governed-summary-icon governed-summary-icon-blue">
              <Bot size={20} />
            </div>
            <div className="governed-summary-card-content">
              <span>Tratadas autonomamente</span>
              <strong>{autonomous_dispositions}</strong>
              <small>
                {formatarPercentual(autonomous_disposition_rate)} das
                disposições do período
              </small>
            </div>
          </article>

          <article className="governed-summary-card">
            <div className="governed-summary-icon governed-summary-icon-purple">
              <UserCheck size={20} />
            </div>
            <div className="governed-summary-card-content">
              <span>Tratadas por decisão humana</span>
              <strong>{human_governed_dispositions}</strong>
              <small>Corredor governado de aprovação</small>
            </div>
          </article>

          <article className="governed-summary-card">
            <div className="governed-summary-icon governed-summary-icon-green">
              <ShieldCheck size={20} />
            </div>
            <div className="governed-summary-card-content">
              <span>Efeito autônomo verificado</span>
              <strong>
                {autonomous_effect_verification.verified}/
                {autonomous_effect_verification.total}
              </strong>
              <small>
                {formatarPercentual(
                  autonomous_effect_verification.verification_rate,
                )}{" "}
                verificado ·{" "}
                {autonomous_effect_verification.not_yet_checked} ainda
                não verificado
              </small>
            </div>
          </article>
        </div>
      </div>

      <div className="governed-summary-block governed-summary-now">
        <div className="governed-summary-block-header">
          <span>Agora</span>
          <strong>Situação atual, fora do período acima</strong>
        </div>

        <article className="governed-summary-now-card">
          <div className="governed-summary-icon governed-summary-icon-orange">
            <AlertTriangle size={20} />
          </div>
          <div className="governed-summary-card-content">
            <span>Contas em aberto e vencidas neste momento</span>
            <strong>{pending_overdue_accounts_now}</strong>
          </div>
        </article>
      </div>
    </section>
  );
}
