import {
  AlertTriangle,
  LoaderCircle,
  Trash2,
  X,
} from "lucide-react";
import {
  useEffect,
  useRef,
} from "react";

import type { Account } from "../../types/account";

import "../../styles/confirm-delete.css";

interface ConfirmDeleteModalProps {
  aberto: boolean;
  cliente: Account | null;
  excluindo?: boolean;
  erro?: string;
  onClose: () => void;
  onConfirm: () => Promise<void> | void;
}

export default function ConfirmDeleteModal({
  aberto,
  cliente,
  excluindo = false,
  erro = "",
  onClose,
  onConfirm,
}: ConfirmDeleteModalProps) {
  const botaoCancelarRef =
    useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!aberto) {
      return;
    }

    const overflowAnterior =
      document.body.style.overflow;

    document.body.style.overflow = "hidden";

    const timeoutId = window.setTimeout(() => {
      botaoCancelarRef.current?.focus();
    }, 0);

    function fecharComEscape(event: KeyboardEvent) {
      if (
        event.key === "Escape" &&
        !excluindo
      ) {
        onClose();
      }
    }

    document.addEventListener(
      "keydown",
      fecharComEscape,
    );

    return () => {
      window.clearTimeout(timeoutId);

      document.removeEventListener(
        "keydown",
        fecharComEscape,
      );

      document.body.style.overflow =
        overflowAnterior;
    };
  }, [aberto, excluindo, onClose]);

  function clicarNoFundo(
    event: React.MouseEvent<HTMLDivElement>,
  ) {
    if (
      event.target === event.currentTarget &&
      !excluindo
    ) {
      onClose();
    }
  }

  if (!aberto || !cliente) {
    return null;
  }

  return (
    <div
      className="confirm-delete-overlay"
      role="presentation"
      onMouseDown={clicarNoFundo}
    >
      <section
        className="confirm-delete-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-delete-title"
        aria-describedby="confirm-delete-description"
      >
        <header className="confirm-delete-header">
          <div className="confirm-delete-icon">
            <AlertTriangle size={26} />
          </div>

          <button
            type="button"
            className="confirm-delete-close"
            aria-label="Fechar confirmação"
            disabled={excluindo}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>

        <div className="confirm-delete-content">
          <span className="confirm-delete-eyebrow">
            Ação permanente
          </span>

          <h2 id="confirm-delete-title">
            Excluir cliente
          </h2>

          <p id="confirm-delete-description">
            Tem certeza de que deseja excluir este cliente?
          </p>

          <div className="confirm-delete-client">
            <span>Cliente selecionado</span>

            <strong>
              {cliente.cliente}
            </strong>
          </div>

          <div className="confirm-delete-warning">
            <Trash2 size={18} />

            <div>
              <strong>
                Esta ação não poderá ser desfeita.
              </strong>

              <span>
                O cliente será removido permanentemente
                da carteira.
              </span>
            </div>
          </div>

          {erro && (
            <div
              className="confirm-delete-error"
              role="alert"
            >
              <AlertTriangle size={19} />

              <span>{erro}</span>
            </div>
          )}
        </div>

        <footer className="confirm-delete-footer">
          <button
            ref={botaoCancelarRef}
            type="button"
            className="confirm-delete-cancel"
            disabled={excluindo}
            onClick={onClose}
          >
            Cancelar
          </button>

          <button
            type="button"
            className="confirm-delete-danger"
            disabled={excluindo}
            onClick={() => void onConfirm()}
          >
            {excluindo ? (
              <>
                <LoaderCircle
                  size={18}
                  className="rotating-icon"
                />

                Excluindo...
              </>
            ) : (
              <>
                <Trash2 size={18} />

                Excluir cliente
              </>
            )}
          </button>
        </footer>
      </section>
    </div>
  );
}