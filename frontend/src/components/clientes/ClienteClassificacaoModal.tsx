import { BadgeCheck, X } from "lucide-react";
import {
  useEffect,
  type MouseEvent,
} from "react";

import type { AccountClassificationResponse } from "../../types/classification";

import "../../styles/classificacao-modal.css";

interface ClienteClassificacaoModalProps {
  aberto: boolean;
  classificacao: AccountClassificationResponse;
  onClose: () => void;
}

const ROTULOS_CLASSIFICACAO: Record<string, string> = {
  PAGAMENTO_REGULAR: "Pagamento regular",
  ATRASO_RECORRENTE: "Atraso recorrente",
  INSUFFICIENT_DATA: "Dados insuficientes",
};

// Fatia 2C -- traducao para texto amigavel do motivo tecnico devolvido
// pela Fatia 2B. Motivo desconhecido cai no proprio texto bruto, para
// nunca esconder informacao caso o backend introduza um motivo novo.

function formatarDataSimples(data: string | null): string {
  if (!data) {
    return "-";
  }

  const [ano, mes, dia] = data.split("-");

  if (!ano || !mes || !dia) {
    return data;
  }

  return `${dia}/${mes}/${ano}`;
}

function formatarDataHora(dataHora: string): string {
  const data = new Date(dataHora);

  if (Number.isNaN(data.getTime())) {
    return dataHora;
  }

  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(data);
}

function formatarProporcao(proporcao: number | null): string {
  if (proporcao === null) {
    return "-";
  }

  return `${Math.round(proporcao * 100)}%`;
}

function traduzirMotivo(
  motivo: string | null,
  minimoExigido: number,
): string {
  if (!motivo) {
    return "-";
  }

  if (motivo === "resolved_occurrences_below_minimum") {
    return (
      `Ocorrências insuficientes para avaliar o padrão ` +
      `(mínimo: ${minimoExigido}).`
    );
  }

  return motivo;
}

export default function ClienteClassificacaoModal({
  aberto,
  classificacao,
  onClose,
}: ClienteClassificacaoModalProps) {
  useEffect(() => {
    if (!aberto) {
      return;
    }

    function fecharComEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    document.addEventListener("keydown", fecharComEscape);

    return () => {
      document.removeEventListener("keydown", fecharComEscape);
    };
  }, [aberto, onClose]);

  useEffect(() => {
    if (!aberto) {
      return;
    }

    const overflowAnterior = document.body.style.overflow;

    document.body.style.overflow = "hidden";

    return () => {
      document.body.style.overflow = overflowAnterior;
    };
  }, [aberto]);

  function clicarNoFundo(event: MouseEvent<HTMLDivElement>) {
    if (event.target === event.currentTarget) {
      onClose();
    }
  }

  if (!aberto) {
    return null;
  }

  const detalhe = classificacao.classification;

  // Guarda defensiva -- este modal só deve ser aberto para
  // status="classified" (garantido pelo chamador em Clientes.tsx), mas
  // nunca renderiza um estado inconsistente caso isso mude.
  if (!detalhe) {
    return null;
  }

  return (
    <div
      className="classificacao-modal-overlay"
      role="presentation"
      onMouseDown={clicarNoFundo}
    >
      <section
        className="classificacao-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="classificacao-modal-title"
      >
        <header className="classificacao-modal-header">
          <div className="classificacao-modal-title-area">
            <div className="classificacao-modal-icon">
              <BadgeCheck size={22} />
            </div>

            <div>
              <h2 id="classificacao-modal-title">
                {ROTULOS_CLASSIFICACAO[detalhe.label] ?? detalhe.label}
              </h2>

              <p>Classificação comportamental do cliente</p>
            </div>
          </div>

          <button
            type="button"
            className="classificacao-modal-close"
            aria-label="Fechar modal"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>

        <div className="classificacao-modal-body">
          <div className="classificacao-field">
            <span>Motivo</span>
            <strong>
              {traduzirMotivo(
                detalhe.reason,
                detalhe.minimum_required_occurrences,
              )}
            </strong>
          </div>

          <div className="classificacao-field">
            <span>Ocorrências analisadas</span>
            <strong>{detalhe.resolved_occurrences}</strong>
          </div>

          <div className="classificacao-field">
            <span>Ocorrências em atraso</span>
            <strong>{detalhe.late_occurrences ?? "-"}</strong>
          </div>

          <div className="classificacao-field">
            <span>Proporção</span>
            <strong>{formatarProporcao(detalhe.late_ratio)}</strong>
          </div>

          <div className="classificacao-field">
            <span>Período observado</span>
            <strong>
              {formatarDataSimples(detalhe.period_start)}
              {" – "}
              {formatarDataSimples(detalhe.period_end)}
            </strong>
          </div>

          <div className="classificacao-field">
            <span>Regra aplicada</span>
            <strong>{detalhe.rule_version}</strong>
          </div>

          <div className="classificacao-field">
            <span>Data da classificação</span>
            <strong>{formatarDataHora(detalhe.classified_at)}</strong>
          </div>
        </div>

        <footer className="classificacao-modal-footer">
          <button
            type="button"
            className="secondary-button"
            onClick={onClose}
          >
            Fechar
          </button>
        </footer>
      </section>
    </div>
  );
}
