import axios from "axios";
import {
  LockKeyhole,
  ShieldCheck,
  WalletCards,
} from "lucide-react";
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

import "./Login.css";

interface LoginLocationState {
  from?: string;
}

function getLoginErrorMessage(
  error: unknown,
): string {
  if (!axios.isAxiosError(error)) {
    return (
      "Não foi possível iniciar a sessão. " +
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
    "Não foi possível iniciar a sessão. " +
    "Verifique suas credenciais."
  );
}

export default function Login() {
  const location = useLocation();
  const navigate = useNavigate();

  const {
    isAuthenticated,
    isLoading,
    signIn,
  } = useAuth();

  const state =
    location.state as
      | LoginLocationState
      | null;

  const requestedPath =
    state?.from &&
    state.from !== "/login"
      ? state.from
      : "/";

  const [
    email,
    setEmail,
  ] = useState("");

  const [
    password,
    setPassword,
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

  if (isAuthenticated) {
    return (
      <Navigate
        to={requestedPath}
        replace
      />
    );
  }

  async function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    if (
      !email.trim() ||
      !password
    ) {
      setErrorMessage(
        "Informe e-mail e senha.",
      );
      return;
    }

    setSubmitting(true);
    setErrorMessage("");

    try {
      await signIn({
        email: email.trim(),
        password,
      });

      navigate(
        requestedPath,
        {
          replace: true,
        },
      );
    } catch (error) {
      setErrorMessage(
        getLoginErrorMessage(error),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-screen">
      <section className="login-panel">
        <div className="login-brand">
          <div className="login-brand-icon">
            <WalletCards size={30} />
          </div>

          <div>
            <strong>
              Auneron AI
            </strong>
            <span>
              Business Intelligence
            </span>
          </div>
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
              Entrar no Auneron
            </h1>

            <p>
              Use sua conta para acessar os
              recursos permitidos ao seu perfil.
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
            <span>E-mail</span>

            <input
              type="email"
              value={email}
              onChange={(event) =>
                setEmail(
                  event.target.value,
                )
              }
              autoComplete="username"
              placeholder="seu@email.com"
              disabled={submitting}
              autoFocus
            />
          </label>

          <label>
            <span>Senha</span>

            <div className="login-password-field">
              <LockKeyhole size={18} />

              <input
                type="password"
                value={password}
                onChange={(event) =>
                  setPassword(
                    event.target.value,
                  )
                }
                autoComplete="current-password"
                placeholder="Digite sua senha"
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
              ? "Entrando..."
              : "Entrar"}
          </button>
        </form>

        <p className="login-security-note">
          Sua senha não é armazenada no navegador.
          A sessão é mantida por cookie HttpOnly.
        </p>
      </section>
    </main>
  );
}
