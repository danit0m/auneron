import {
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  Badge,
  Button,
  Input,
} from "../ui";
import {
  useElevation,
} from "../../hooks/useElevation";

import "./ElevatedAccessModal.css";

interface ElevatedAccessModalProps {
  open: boolean;
  resourceLabel: string;
  onCancel: () => void;
}

export function ElevatedAccessModal({
  open,
  resourceLabel,
  onCancel,
}: ElevatedAccessModalProps) {
  const {
    status,
    requestElevation,
  } = useElevation();

  const [
    credential,
    setCredential,
  ] = useState("");

  const [
    error,
    setError,
  ] = useState("");

  const [
    showCredential,
    setShowCredential,
  ] = useState(false);

  const inputRef =
    useRef<HTMLInputElement | null>(
      null,
    );

  const validating =
    status === "validating";

  const resetForm =
    useCallback(() => {
      setCredential("");
      setError("");
      setShowCredential(false);
    }, []);

  const handleCancel =
    useCallback(() => {
      resetForm();
      onCancel();
    }, [
      onCancel,
      resetForm,
    ]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const previousOverflow =
      document.body.style.overflow;

    document.body.style.overflow =
      "hidden";

    const timeoutId =
      window.setTimeout(() => {
        inputRef.current?.focus();
      }, 0);

    function handleEscape(
      event: KeyboardEvent,
    ) {
      if (
        event.key === "Escape" &&
        !validating
      ) {
        handleCancel();
      }
    }

    document.addEventListener(
      "keydown",
      handleEscape,
    );

    return () => {
      window.clearTimeout(
        timeoutId,
      );

      document.removeEventListener(
        "keydown",
        handleEscape,
      );

      document.body.style.overflow =
        previousOverflow;
    };
  }, [
    open,
    validating,
    handleCancel,
  ]);

  if (!open) {
    return null;
  }

  async function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();
    setError("");

    const result =
      await requestElevation(
        credential,
      );

    if (!result.success) {
      setError(result.message);
      return;
    }

    resetForm();
  }

  return (
    <div
      className="elevated-access-overlay"
      role="presentation"
    >
      <section
        className="elevated-access-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="elevated-access-title"
        aria-describedby="elevated-access-description"
      >
        <header className="elevated-access-header">
          <div className="elevated-access-icon">
            <LockKeyhole size={26} />
          </div>

          <button
            type="button"
            className="elevated-access-close"
            aria-label="Cancelar validação elevada"
            disabled={validating}
            onClick={handleCancel}
          >
            <X size={20} />
          </button>
        </header>

        <form
          className="elevated-access-form"
          onSubmit={handleSubmit}
        >
          <div className="elevated-access-title">
            <Badge
              variant="warning"
              icon={
                <KeyRound size={14} />
              }
            >
              Acesso elevado
            </Badge>

            <h1 id="elevated-access-title">
              Confirme sua senha
            </h1>

            <p id="elevated-access-description">
              O recurso{" "}
              <strong>
                {resourceLabel}
              </strong>{" "}
              contém funções administrativas
              sensíveis.
            </p>
          </div>

          <div className="elevated-access-security-note">
            <ShieldCheck size={20} />

            <div>
              <strong>
                Validação no servidor
              </strong>

              <span>
                Sua senha será validada pelo
                backend. A elevação é temporária
                e vinculada à sua sessão atual.
              </span>
            </div>
          </div>

          <Input
            ref={inputRef}
            label="Senha da conta"
            type={
              showCredential
                ? "text"
                : "password"
            }
            autoComplete="current-password"
            placeholder="Digite sua senha"
            value={credential}
            error={error}
            disabled={validating}
            startIcon={
              <KeyRound size={17} />
            }
            endIcon={
              <button
                type="button"
                className="elevated-access-visibility"
                aria-label={
                  showCredential
                    ? "Ocultar senha"
                    : "Mostrar senha"
                }
                disabled={validating}
                onClick={() =>
                  setShowCredential(
                    (current) =>
                      !current,
                  )
                }
              >
                {showCredential ? (
                  <EyeOff size={17} />
                ) : (
                  <Eye size={17} />
                )}
              </button>
            }
            onChange={(event) => {
              setCredential(
                event.target.value,
              );

              if (error) {
                setError("");
              }
            }}
          />

          <footer className="elevated-access-footer">
            <Button
              variant="secondary"
              disabled={validating}
              onClick={handleCancel}
            >
              Cancelar
            </Button>

            <Button
              type="submit"
              disabled={
                credential.length === 0 ||
                validating
              }
              startIcon={
                validating ? (
                  <LoaderCircle
                    size={17}
                    className="elevated-access-spinner"
                  />
                ) : (
                  <ShieldCheck size={17} />
                )
              }
            >
              {validating
                ? "Validando..."
                : "Confirmar acesso"}
            </Button>
          </footer>
        </form>
      </section>
    </div>
  );
}
