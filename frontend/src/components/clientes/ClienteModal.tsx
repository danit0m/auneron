import {
  AlertCircle,
  LoaderCircle,
  Save,
  UserPlus,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type MouseEvent,
  useEffect,
  useState,
} from "react";

import type {
  Account,
  AccountCreate,
  AccountStatus,
} from "../../types/account";

import "../../styles/cliente-modal.css";

interface ClienteModalProps {
  aberto: boolean;
  cliente?: Account | null;
  salvando?: boolean;
  erro?: string;
  onClose: () => void;
  onSubmit: (
    dados: AccountCreate,
  ) => Promise<void> | void;
}

interface ClienteFormulario {
  cliente: string;
  email: string;
  whatsapp: string;
  valor: number;
  vencimento: string;
  status: AccountStatus;
}

type CampoFormulario =
  keyof ClienteFormulario;

const FORMULARIO_INICIAL: ClienteFormulario = {
  cliente: "",
  email: "",
  whatsapp: "",
  valor: 0,
  vencimento: "",
  status: "aberto",
};

export default function ClienteModal({
  aberto,
  cliente = null,
  salvando = false,
  erro = "",
  onClose,
  onSubmit,
}: ClienteModalProps) {
  const [formulario, setFormulario] =
    useState<ClienteFormulario>({
      ...FORMULARIO_INICIAL,
    });

  const [errosCampos, setErrosCampos] =
    useState<
      Partial<
        Record<CampoFormulario, string>
      >
    >({});

  const modoEdicao = Boolean(cliente);

  useEffect(() => {
    if (!aberto) {
      return;
    }

    if (cliente) {
      setFormulario({
        cliente: cliente.cliente,
        email: cliente.email ?? "",
        whatsapp:
          cliente.whatsapp ?? "",
        valor: Number(cliente.valor),
        vencimento:
          cliente.vencimento ?? "",
        status: normalizarStatus(
          cliente.status,
        ),
      });
    } else {
      setFormulario({
        ...FORMULARIO_INICIAL,
      });
    }

    setErrosCampos({});
  }, [aberto, cliente]);

  useEffect(() => {
    if (!aberto) {
      return;
    }

    function fecharComEscape(
      event: KeyboardEvent,
    ) {
      if (
        event.key === "Escape" &&
        !salvando
      ) {
        onClose();
      }
    }

    document.addEventListener(
      "keydown",
      fecharComEscape,
    );

    return () => {
      document.removeEventListener(
        "keydown",
        fecharComEscape,
      );
    };
  }, [aberto, onClose, salvando]);

  useEffect(() => {
    if (!aberto) {
      return;
    }

    const overflowAnterior =
      document.body.style.overflow;

    document.body.style.overflow =
      "hidden";

    return () => {
      document.body.style.overflow =
        overflowAnterior;
    };
  }, [aberto]);

  function atualizarCampo<
    T extends CampoFormulario,
  >(
    campo: T,
    valor: ClienteFormulario[T],
  ) {
    setFormulario((estadoAtual) => ({
      ...estadoAtual,
      [campo]: valor,
    }));

    setErrosCampos(
      (estadoAtual) => ({
        ...estadoAtual,
        [campo]: undefined,
      }),
    );
  }

  function validarFormulario(): boolean {
    const novosErros: Partial<
      Record<CampoFormulario, string>
    > = {};

    const nomeCliente =
      formulario.cliente.trim();

    const email =
      formulario.email.trim();

    if (!nomeCliente) {
      novosErros.cliente =
        "Informe o nome do cliente.";
    } else if (
      nomeCliente.length < 2
    ) {
      novosErros.cliente =
        "O nome deve possuir pelo menos 2 caracteres.";
    }

    if (email) {
      const emailValido =
        /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

      if (
        !emailValido.test(email)
      ) {
        novosErros.email =
          "Informe um e-mail válido.";
      }
    }

    if (
      !Number.isFinite(
        formulario.valor,
      ) ||
      formulario.valor <= 0
    ) {
      novosErros.valor =
        "O valor deve ser maior que zero.";
    }

    if (!formulario.vencimento) {
      novosErros.vencimento =
        "Informe a data de vencimento.";
    }

    if (
      ![
        "aberto",
        "pago",
        "atrasado",
      ].includes(formulario.status)
    ) {
      novosErros.status =
        "Selecione um status válido.";
    }

    setErrosCampos(novosErros);

    return (
      Object.keys(novosErros)
        .length === 0
    );
  }

  async function enviarFormulario(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    if (!validarFormulario()) {
      return;
    }

    const emailTratado =
      formulario.email.trim();

    const whatsappTratado =
      formulario.whatsapp.trim();

    const dados: AccountCreate = {
      cliente:
        formulario.cliente.trim(),

      email:
        emailTratado.length > 0
          ? emailTratado
          : null,

      whatsapp:
        whatsappTratado.length > 0
          ? whatsappTratado
          : null,

      valor: Number(
        formulario.valor,
      ),

      vencimento:
        formulario.vencimento,

      status:
        formulario.status,
    };

    await onSubmit(dados);
  }

  function clicarNoFundo(
    event: MouseEvent<HTMLDivElement>,
  ) {
    if (
      event.target ===
        event.currentTarget &&
      !salvando
    ) {
      onClose();
    }
  }

  if (!aberto) {
    return null;
  }

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onMouseDown={clicarNoFundo}
    >
      <section
        className="cliente-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cliente-modal-title"
      >
        <header className="cliente-modal-header">
          <div className="cliente-modal-title-area">
            <div className="cliente-modal-icon">
              <UserPlus size={22} />
            </div>

            <div>
              <h2 id="cliente-modal-title">
                {modoEdicao
                  ? "Editar cliente"
                  : "Novo cliente"}
              </h2>

              <p>
                {modoEdicao
                  ? "Atualize os dados financeiros do cliente."
                  : "Cadastre um novo cliente na sua carteira."}
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

        <form
          className="cliente-modal-form"
          onSubmit={enviarFormulario}
        >
          {erro && (
            <div className="cliente-modal-error">
              <AlertCircle size={19} />

              <span>{erro}</span>
            </div>
          )}

          <div className="cliente-form-grid">
            <div className="cliente-form-field cliente-form-full">
              <label htmlFor="cliente">
                Nome do cliente
                <strong>*</strong>
              </label>

              <input
                id="cliente"
                type="text"
                value={
                  formulario.cliente
                }
                placeholder="Ex.: Empresa Rocha SA"
                autoFocus
                disabled={salvando}
                className={
                  errosCampos.cliente
                    ? "cliente-input-error"
                    : ""
                }
                onChange={(event) =>
                  atualizarCampo(
                    "cliente",
                    event.target.value,
                  )
                }
              />

              {errosCampos.cliente && (
                <small>
                  {
                    errosCampos.cliente
                  }
                </small>
              )}
            </div>

            <div className="cliente-form-field">
              <label htmlFor="email">
                E-mail
              </label>

              <input
                id="email"
                type="email"
                value={
                  formulario.email
                }
                placeholder="financeiro@empresa.com"
                disabled={salvando}
                className={
                  errosCampos.email
                    ? "cliente-input-error"
                    : ""
                }
                onChange={(event) =>
                  atualizarCampo(
                    "email",
                    event.target.value,
                  )
                }
              />

              {errosCampos.email && (
                <small>
                  {errosCampos.email}
                </small>
              )}
            </div>

            <div className="cliente-form-field">
              <label htmlFor="whatsapp">
                WhatsApp
              </label>

              <input
                id="whatsapp"
                type="text"
                value={
                  formulario.whatsapp
                }
                placeholder="+55 11 99999-9999"
                disabled={salvando}
                onChange={(event) =>
                  atualizarCampo(
                    "whatsapp",
                    event.target.value,
                  )
                }
              />
            </div>

            <div className="cliente-form-field">
              <label htmlFor="valor">
                Valor
                <strong>*</strong>
              </label>

              <div className="cliente-money-input">
                <span>R$</span>

                <input
                  id="valor"
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={
                    formulario.valor === 0
                      ? ""
                      : formulario.valor
                  }
                  placeholder="0,00"
                  disabled={salvando}
                  className={
                    errosCampos.valor
                      ? "cliente-input-error"
                      : ""
                  }
                  onChange={(event) =>
                    atualizarCampo(
                      "valor",
                      event.target.value === ""
                        ? 0
                        : Number(
                            event.target
                              .value,
                          ),
                    )
                  }
                />
              </div>

              {errosCampos.valor && (
                <small>
                  {errosCampos.valor}
                </small>
              )}
            </div>

            <div className="cliente-form-field">
              <label htmlFor="vencimento">
                Vencimento
                <strong>*</strong>
              </label>

              <input
                id="vencimento"
                type="date"
                value={
                  formulario.vencimento
                }
                disabled={salvando}
                className={
                  errosCampos.vencimento
                    ? "cliente-input-error"
                    : ""
                }
                onChange={(event) =>
                  atualizarCampo(
                    "vencimento",
                    event.target.value,
                  )
                }
              />

              {errosCampos.vencimento && (
                <small>
                  {
                    errosCampos.vencimento
                  }
                </small>
              )}
            </div>

            <div className="cliente-form-field cliente-form-full">
              <label htmlFor="status">
                Status financeiro
                <strong>*</strong>
              </label>

              <select
                id="status"
                value={
                  formulario.status
                }
                disabled={salvando}
                className={
                  errosCampos.status
                    ? "cliente-input-error"
                    : ""
                }
                onChange={(event) =>
                  atualizarCampo(
                    "status",
                    event.target
                      .value as AccountStatus,
                  )
                }
              >
                <option value="aberto">
                  Em aberto
                </option>

                <option value="pago">
                  Pago
                </option>

                <option value="atrasado">
                  Atrasado
                </option>
              </select>

              {errosCampos.status && (
                <small>
                  {errosCampos.status}
                </small>
              )}
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
                  <LoaderCircle
                    size={18}
                    className="rotating-icon"
                  />

                  Salvando...
                </>
              ) : (
                <>
                  <Save size={18} />

                  {modoEdicao
                    ? "Salvar alterações"
                    : "Cadastrar cliente"}
                </>
              )}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

function normalizarStatus(
  status: string,
): AccountStatus {
  const statusNormalizado =
    status.trim().toLowerCase();

  if (
    statusNormalizado === "pago" ||
    statusNormalizado === "atrasado"
  ) {
    return statusNormalizado;
  }

  return "aberto";
}