import {
  AlertCircle,
  LoaderCircle,
  PhoneCall,
  Save,
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
  MAX_NOTA,
  ROTULO_TIPO,
  montarEventoContato,
  tipoExigeData,
  tiposDisponiveis,
  type TipoEventoContato,
} from "../../lib/contatoPromessa";
import { dataIsoLocal } from "../../lib/pagamento";
import type { Account } from "../../types/account";

import "../../styles/cliente-modal.css";
import "../../styles/pagamento-modal.css";

interface RegistrarContatoModalProps {
  conta: Account;
  salvando?: boolean;
  /** Mensagem de erro da API, já em português. */
  erro?: string;
  onClose: () => void;
  /** A chave de idempotência vale por abertura do modal e por conteúdo informado. */
  onSubmit: (dados: {
    corpo: {
      event_type: TipoEventoContato;
      promised_date?: string;
      note?: string;
    };
    idempotencyKey: string;
  }) => Promise<void> | void;
}

export default function RegistrarContatoModal({
  conta,
  salvando = false,
  erro = "",
  onClose,
  onSubmit,
}: RegistrarContatoModalProps) {
  const tipos = useMemo(() => tiposDisponiveis(conta), [conta]);
  const [tipo, setTipo] = useState<TipoEventoContato>(tipos[0]);
  const [data, setData] = useState("");
  const [nota, setNota] = useState("");
  const [chave, setChave] = useState(() => createIdempotencyKey());
  const [erroForm, setErroForm] = useState("");

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

  // Outro conteúdo é outra intenção: chave nova (a mesma chave com outro
  // conteúdo seria recusada como conflito de idempotência).
  function alterar(acao: () => void) {
    acao();
    setErroForm("");
    setChave(createIdempotencyKey());
  }

  async function enviar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (salvando) {
      return;
    }

    const resultado = montarEventoContato({ tipo, data, nota });

    if (!resultado.valido) {
      setErroForm(resultado.erro);
      return;
    }

    await onSubmit({ corpo: resultado.corpo, idempotencyKey: chave });
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
        aria-labelledby="contato-modal-title"
      >
        <header className="cliente-modal-header">
          <div className="cliente-modal-title-area">
            <div className="cliente-modal-icon">
              <PhoneCall size={22} />
            </div>

            <div>
              <h2 id="contato-modal-title">Contato e promessa</h2>

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
          {(erro || erroForm) && (
            <div className="cliente-modal-error" role="alert">
              <AlertCircle size={19} />

              <span>{erroForm || erro}</span>
            </div>
          )}

          <div className="cliente-form-field">
            <label htmlFor="contato-tipo">O que aconteceu</label>

            <select
              id="contato-tipo"
              value={tipo}
              disabled={salvando}
              onChange={(event) =>
                alterar(() => setTipo(event.target.value as TipoEventoContato))
              }
            >
              {tipos.map((t) => (
                <option key={t} value={t}>
                  {ROTULO_TIPO[t]}
                </option>
              ))}
            </select>
          </div>

          {tipoExigeData(tipo) && (
            <div className="cliente-form-field">
              <label htmlFor="contato-data">Data prometida</label>

              <input
                id="contato-data"
                type="date"
                value={data}
                min={dataIsoLocal(new Date())}
                required
                disabled={salvando}
                onChange={(event) => alterar(() => setData(event.target.value))}
              />
            </div>
          )}

          <div className="cliente-form-field">
            <label htmlFor="contato-nota">Observação (opcional)</label>

            <input
              id="contato-nota"
              type="text"
              value={nota}
              maxLength={MAX_NOTA}
              disabled={salvando}
              onChange={(event) => alterar(() => setNota(event.target.value))}
            />
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
                  Salvar
                </>
              )}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}
