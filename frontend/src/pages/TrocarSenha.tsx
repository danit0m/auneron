import axios from "axios";
import {
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";
import { BrandLogo } from "../components/brand/BrandLogo";
import {
  type FormEvent,
  useState,
} from "react";
import {
  Navigate,
  useLocation,
  useNavigate,
} from "react-router-dom";

import {
  useAuth,
} from "../hooks/useAuth";
import {
  destinoAposTroca,
  precisaTrocarSenha,
  validarTrocaDeSenha,
} from "../lib/trocaSenha";

import "./Login.css";

interface TrocarSenhaLocationState {
  from?: string;
}

function getChangePasswordErrorMessage(
  error: unknown,
): string {
  if (!axios.isAxiosError(error)) {
    return (
      "Não foi possível trocar a senha. " +
      "Tente novamente."
    );
  }

  if (!error.response) {
    return (
      "Não foi possível conectar ao Auneron. " +
      "Verifique se os serviços estão em execução."
    );
  }

  const detail =
    error.response.data &&
    typeof error.response.data === "object" &&
    "detail" in error.response.data
      ? error.response.data.detail
      : null;

  if (typeof detail === "string") {
    return detail;
  }

  return (
    "Não foi possível trocar a senha. " +
    "Confira os dados informados."
  );
}

export default function TrocarSenha() {
  const location = useLocation();
  const navigate = useNavigate();

  const {
    user,
    isAuthenticated,
    isLoading,
    changePassword,
    signOut,
  } = useAuth();

  const state =
    location.state as
      | TrocarSenhaLocationState
      | null;

  const [
    currentPassword,
    setCurrentPassword,
  ] = useState("");

  const [
    newPassword,
    setNewPassword,
  ] = useState("");

  const [
    confirmation,
    setConfirmation,
  ] = useState("");

  const [
    submitting,
    setSubmitting,
  ] = useState(false);

  const [
    errorMessage,
    setErrorMessage,
  ] = useState("");

  if (isLoading) {
    return (
      <div className="login-screen">
        <div className="login-loading">
          <div className="loading-spinner" />
          <span>
            Verificando sessão...
          </span>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <Navigate
        to="/login"
        replace
      />
    );
  }

  const obrigatoria =
    precisaTrocarSenha(user);

  async function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    const validation =
      validarTrocaDeSenha(
        currentPassword,
        newPassword,
        confirmation,
      );

    if (validation) {
      setErrorMessage(validation);
      return;
    }

    setSubmitting(true);
    setErrorMessage("");

    try {
      await changePassword(
        currentPassword,
        newPassword,
      );

      navigate(
        destinoAposTroca(state?.from),
        {
          replace: true,
        },
      );
    } catch (error) {
      setErrorMessage(
        getChangePasswordErrorMessage(
          error,
        ),
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSignOut() {
    await signOut();
    navigate("/login", {
      replace: true,
    });
  }

  return (
    <main className="login-screen">
      <section className="login-panel">
        <div className="login-brand login-brand--d2">
          <BrandLogo variant="horizontal" />
          <span className="login-brand-tagline">
            Inteligência que trabalha pela sua empresa.
          </span>
        </div>

        <div className="login-heading">
          <div className="login-security-icon">
            <ShieldCheck size={28} />
          </div>

          <div>
            <span className="login-eyebrow">
              ACESSO SEGURO
            </span>

            <h1>
              Trocar senha
            </h1>

            <p>
              {obrigatoria
                ? "Você entrou com uma senha temporária. " +
                  "Defina uma nova senha para continuar."
                : "Informe a senha atual e defina uma nova senha."}
            </p>
          </div>
        </div>

        <form
          className="login-form"
          onSubmit={
            (event) =>
              void handleSubmit(event)
          }
        >
          <label>
            <span>
              {obrigatoria
                ? "Senha temporária"
                : "Senha atual"}
            </span>

            <div className="login-password-field">
              <LockKeyhole size={18} />

              <input
                type="password"
                value={currentPassword}
                onChange={(event) =>
                  setCurrentPassword(
                    event.target.value,
                  )
                }
                autoComplete="current-password"
                disabled={submitting}
                autoFocus
              />
            </div>
          </label>

          <label>
            <span>Nova senha</span>

            <div className="login-password-field">
              <LockKeyhole size={18} />

              <input
                type="password"
                value={newPassword}
                onChange={(event) =>
                  setNewPassword(
                    event.target.value,
                  )
                }
                autoComplete="new-password"
                placeholder="Mínimo de 12 caracteres"
                disabled={submitting}
              />
            </div>
          </label>

          <label>
            <span>
              Confirme a nova senha
            </span>

            <div className="login-password-field">
              <LockKeyhole size={18} />

              <input
                type="password"
                value={confirmation}
                onChange={(event) =>
                  setConfirmation(
                    event.target.value,
                  )
                }
                autoComplete="new-password"
                disabled={submitting}
              />
            </div>
          </label>

          {errorMessage && (
            <div
              className="login-error"
              role="alert"
            >
              {errorMessage}
            </div>
          )}

          <button
            type="submit"
            className="login-submit"
            disabled={submitting}
          >
            {submitting
              ? "Salvando..."
              : "Trocar senha"}
          </button>

          <button
            type="button"
            className="login-submit"
            disabled={submitting}
            onClick={
              () => void handleSignOut()
            }
          >
            Sair
          </button>
        </form>

        <p className="login-security-note">
          Ao trocar a senha, as outras sessões
          desta conta são encerradas.
        </p>
      </section>
    </main>
  );
}
