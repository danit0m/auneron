import {
  AlertCircle,
  AlertTriangle,
  LoaderCircle,
  Save,
  UserPlus,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type MouseEvent,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { Account } from "../../types/account";
import {
  contatosDistintos,
  formularioInicial,
  validarContato,
  type ErrosContato,
} from "../../lib/contatoCliente";

import "../../styles/cliente-modal.css";
import "../../styles/contato-cliente-modal.css";

interface ContatoClienteModalProps {
  /** Nome do cliente (exibição e chave do PUT). */
  cliente: string;
  /** Todos os títulos do cliente carregados na tela. */
  titulos: Account[];
  salvando?: boolean;
  erro?: string;
  onClose: () => void;
  onSubmit: (dados: {
    cliente: string;
    email: string | null;
    whatsapp: string | null;
  }) => Promise<void> | void;
}

export default function ContatoClienteModal({
  cliente,
  titulos,
  salvando = false,
  erro = "",
  onClose,
  onSubmit,
}: ContatoClienteModalProps) {
  const [formulario, setFormulario] = useState(() =>
    formularioInicial(titulos),
  );
  const [erros, setErros] = useState<ErrosContato>({});

  const distintos = useMemo(
    () => contatosDistintos(titulos),
    [titulos],
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

  function atualizar(campo: "email" | "whatsapp", valor: string) {
    setFormulario((atual) => ({ ...atual, [campo]: valor }));
    setErros({});
  }

  async function enviar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (salvando) {
      return;
    }

    const resultado = validarContato(formulario);

    if (!resultado.valido) {
      setErros(resultado.erros);
      return;
    }

    await onSubmit({
      cliente,
      email: resultado.email,
      whatsapp: resultado.whatsapp,
    });
  }

  function clicarNoFundo(event: MouseEvent<HTMLDivElement>) {
    if (event.target === event.currentTarget && !salvando) {
      onClose();
    }
  }

  const mensagemErro = erro || erros.geral || "";

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onMouseDown={clicarNoFundo}
    >
      <section
        className="cliente-modal contato-cliente-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="contato-cliente-title"
      >
        <header className="cliente-modal-header">
          <div className="cliente-modal-title-area">
            <div className="cliente-modal-icon">
              <UserPlus size={22} />
            </div>

            <div>
              <h2 id="contato-cliente-title">Completar contato</h2>

              <p>
                {cliente} · vale para os {titulos.length}{" "}
                {titulos.length === 1 ? "título" : "títulos"} do
                cliente.
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
          {mensagemErro && (
            <div className="cliente-modal-error" role="alert">
              <AlertCircle size={19} />

              <span>{mensagemErro}</span>
            </div>
          )}

          {distintos.divergente && (
            <div className="contato-cliente-aviso" role="status">
              <AlertTriangle size={19} />

              <div>
                <strong>
                  Os títulos deste cliente têm contatos diferentes.
                </strong>

                <span>
                  Ao salvar, o valor informado substitui o de todos os
                  títulos.
                </span>

                {distintos.emails.length > 1 && (
                  <span>
                    E-mails encontrados: {distintos.emails.join(", ")}
                  </span>
                )}

                {distintos.whatsapps.length > 1 && (
                  <span>
                    WhatsApps encontrados:{" "}
                    {distintos.whatsapps.join(", ")}
                  </span>
                )}
              </div>
            </div>
          )}

          <div className="cliente-form-grid contato-cliente-grid">
            <div className="cliente-form-field">
              <label htmlFor="contato-email">E-mail</label>

              <input
                id="contato-email"
                type="email"
                value={formulario.email}
                placeholder="financeiro@empresa.com"
                autoFocus
                disabled={salvando}
                aria-invalid={Boolean(erros.email)}
                className={erros.email ? "cliente-input-error" : ""}
                onChange={(event) =>
                  atualizar("email", event.target.value)
                }
              />

              {erros.email && <small>{erros.email}</small>}
            </div>

            <div className="cliente-form-field">
              <label htmlFor="contato-whatsapp">WhatsApp</label>

              <input
                id="contato-whatsapp"
                type="text"
                inputMode="tel"
                value={formulario.whatsapp}
                placeholder="+55 11 99999-9999"
                disabled={salvando}
                aria-invalid={Boolean(erros.whatsapp)}
                className={erros.whatsapp ? "cliente-input-error" : ""}
                onChange={(event) =>
                  atualizar("whatsapp", event.target.value)
                }
              />

              {erros.whatsapp && <small>{erros.whatsapp}</small>}
            </div>
          </div>

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
                  Salvando...
                </>
              ) : (
                <>
                  <Save size={18} />
                  Salvar contato
                </>
              )}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}
