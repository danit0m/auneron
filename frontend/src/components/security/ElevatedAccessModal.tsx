import {
  AlertTriangle,
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
  ELEVATION_DURATION_MINUTES,
} from "../../security/elevation";
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
    isDevelopmentElevation,
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
        onCancel();
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
    onCancel,
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

    setCredential("");
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
            onClick={onCancel}
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
              Elevated Credentials
            </Badge>

            <h1 id="elevated-access-title">
              Credencial elevada necessária
            </h1>

            <p id="elevated-access-description">
              O recurso{" "}
              <strong>
                {resourceLabel}
              </strong>{" "}
              contém funções administrativas sensíveis.
            </p>
          </div>

          <div className="elevated-access-security-note">
            <ShieldCheck size={20} />

            <div>
              <strong>
                Elevação temporária
              </strong>

              <span>
                Após a validação, o acesso permanecerá
                elevado por{" "}
                {ELEVATION_DURATION_MINUTES} minutos
                nesta aba.
              </span>
            </div>
          </div>

          {isDevelopmentElevation && (
            <div className="elevated-access-development">
              <AlertTriangle size={18} />

              <span>
                Ambiente de desenvolvimento. A validação
                utiliza VITE_ELEVATED_DEV_CODE e não
                substitui segurança no backend.
              </span>
            </div>
          )}

          <Input
            ref={inputRef}
            label="Credencial elevada"
            type={
              showCredential
                ? "text"
                : "password"
            }
            autoComplete="current-password"
            placeholder="Informe a credencial"
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
                    ? "Ocultar credencial"
                    : "Mostrar credencial"
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
              onClick={onCancel}
            >
              Cancelar
            </Button>

            <Button
              type="submit"
              disabled={
                credential.trim().length ===
                  0 ||
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
                : "Validar credencial"}
            </Button>
          </footer>
        </form>
      </section>
    </div>
  );
}
