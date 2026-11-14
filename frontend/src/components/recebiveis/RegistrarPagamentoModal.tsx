import {
  AlertCircle,
  AlertTriangle,
  Banknote,
  LoaderCircle,
  Send,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type MouseEvent,
  useEffect,
  useMemo,
  useState,
} from "react";

import { createIdempotencyKey } from "../../api/api";
import {
  avisoDataAnteriorAoCadastro,
  dataIsoLocal,
  validarDataPagamento,
} from "../../lib/pagamento";
import type { Account } from "../../types/account";

import "../../styles/cliente-modal.css";
import "../../styles/pagamento-modal.css";

interface RegistrarPagamentoModalProps {
  conta: Account;
  salvando?: boolean;
  /** Mensagem de erro da API, já em português. */
  erro?: string;
  onClose: () => void;
  /** A chave de idempotência vale por abertura do modal e por data informada. */
  onSubmit: (dados: {
    paidOn: string;
    idempotencyKey: string;
  }) => Promise<void> | void;
}

export default function RegistrarPagamentoModal({
  conta,
  salvando = false,
  erro = "",
  onClose,
  onSubmit,
}: RegistrarPagamentoModalProps) {
  const [dataPagamento, setDataPagamento] = useState(() =>
    dataIsoLocal(new Date()),
  );
  const [chave, setChave] = useState(() => createIdempotencyKey());
  const [erroData, setErroData] = useState("");

  const aviso = useMemo(
    () => avisoDataAnteriorAoCadastro(dataPagamento, conta.created_at),
    [dataPagamento, conta.created_at],
  );

  useEffect(() => {
    function fecharComEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !salvando) {
        onClose();
      }
    }

    document.addEventListener("keydown", fecharComEscape);

    return () => {
      document.removeEventListener("keydown", fecharComEscape);
    };
  }, [onClose, salvando]);

  useEffect(() => {
    const overflowAnterior = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.body.style.overflow = overflowAnterior;
    };
  }, []);

  function atualizarData(valor: string) {
    setDataPagamento(valor);
    setErroData("");
    // Outra data é outra intenção: chave nova (a mesma chave com outro
    // conteúdo seria recusada como conflito de idempotência).
    setChave(createIdempotencyKey());
  }

  async function enviar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (salvando) {
      return;
    }

    const resultado = validarDataPagamento(dataPagamento, new Date());

    if (!resultado.valida) {
      setErroData(resultado.erro);
      return;
    }

    await onSubmit({ paidOn: dataPagamento, idempotencyKey: chave });
  }

  function clicarNoFundo(event: MouseEvent<HTMLDivElement>) {
    if (event.target === event.currentTarget && !salvando) {
      onClose();
    }
  }

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onMouseDown={clicarNoFundo}
    >
      <section
        className="cliente-modal pagamento-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pagamento-modal-title"
      >
        <header className="cliente-modal-header">
          <div className="cliente-modal-title-area">
            <div className="cliente-modal-icon">
              <Banknote size={22} />
            </div>

            <div>
              <h2 id="pagamento-modal-title">Registrar pagamento</h2>

              <p>
                {conta.cliente} · vencimento{" "}
                {conta.vencimento.split("-").reverse().join("/")}
              </p>
            </div>
          </div>

          <button
            type="button"
            className="cliente-modal-close"
            aria-label="Fechar modal"
            disabled={salvando}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>

        <form className="cliente-modal-form" onSubmit={enviar} noValidate>
          {erro && (
            <div className="cliente-modal-error" role="alert">
              <AlertCircle size={19} />

              <span>{erro}</span>
            </div>
          )}

          <p className="pagamento-modal-explicacao">
            O pedido segue para aprovação de outra pessoa. O título só passa a
            pago depois da aprovação e da conclusão.
          </p>

          <div className="cliente-form-field">
            <label htmlFor="pagamento-data">Data do pagamento</label>

            <input
              id="pagamento-data"
              type="date"
              value={dataPagamento}
              max={dataIsoLocal(new Date())}
              required
              autoFocus
              disabled={salvando}
              aria-invalid={Boolean(erroData)}
              aria-describedby={
                erroData ? "pagamento-data-erro" : undefined
              }
              className={erroData ? "cliente-input-error" : ""}
              onChange={(event) => atualizarData(event.target.value)}
            />

            {erroData && (
              <small id="pagamento-data-erro">{erroData}</small>
            )}
          </div>

          {aviso && (
            <div className="pagamento-modal-aviso" role="status">
              <AlertTriangle size={19} />

              <span>{aviso}</span>
            </div>
          )}

          <footer className="cliente-modal-footer">
            <button
              type="button"
              className="secondary-button"
              disabled={salvando}
              onClick={onClose}
            >
              Cancelar
            </button>

            <button
              type="submit"
              className="primary-button"
              disabled={salvando}
            >
              {salvando ? (
                <>
                  <LoaderCircle size={18} className="rotating-icon" />
                  Enviando...
                </>
              ) : (
                <>
                  <Send size={18} />
                  Enviar para aprovação
                </>
              )}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}
