import {
  AlertTriangle,
  LoaderCircle,
  Trash2,
  X,
} from "lucide-react";
import { useEffect } from "react";

import type { Account } from "../../types/account";

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
  useEffect(() => {
    if (!aberto) {
      return;
    }

    function fecharComEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !excluindo) {
        onClose();
      }
    }

    document.addEventListener("keydown", fecharComEscape);

    return () => {
      document.removeEventListener("keydown", fecharComEscape);
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
      className="modal-overlay"
      role="presentation"
      onMouseDown={clicarNoFundo}
    >
      <section
        className="confirm-delete-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-delete-title"
      >
        <header className="confirm-delete-header">
          <div className="confirm-delete-icon">
            <AlertTriangle size={24} />
          </div>

          <button
            type="button"
            className="cliente-modal-close"
            aria-label="Fechar confirmação"
            disabled={excluindo}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>

        <div className="confirm-delete-content">
          <h2 id="confirm-delete-title">
            Excluir cliente
          </h2>

          <p>
            Tem certeza de que deseja excluir o cliente{" "}
            <strong>{cliente.cliente}</strong>?
          </p>

          <div className="confirm-delete-warning">
            <Trash2 size={18} />

            <span>
              Esta ação não poderá ser desfeita.
            </span>
          </div>

          {erro && (
            <div className="cliente-modal-error">
              <AlertTriangle size={19} />
              <span>{erro}</span>
            </div>
          )}
        </div>

        <footer className="confirm-delete-footer">
          <button
            type="button"
            className="secondary-button"
            disabled={excluindo}
            onClick={onClose}
          >
            Cancelar
          </button>

          <button
            type="button"
            className="danger-button"
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