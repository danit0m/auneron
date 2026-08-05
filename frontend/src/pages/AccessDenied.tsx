import {
  LockKeyhole,
  ShieldAlert,
} from "lucide-react";
import {
  useLocation,
  useNavigate,
} from "react-router-dom";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
} from "../components/ui";

interface AccessDeniedState {
  from?: string;
  reason?:
    | "not-authenticated"
    | "insufficient-permission";
}

export default function AccessDenied() {
  const navigate = useNavigate();
  const location = useLocation();

  const state =
    location.state as
      | AccessDeniedState
      | null;

  const notAuthenticated =
    state?.reason ===
    "not-authenticated";

  return (
    <div className="page">
      <section
        className="page-content"
        style={{
          display: "grid",
          minHeight:
            "calc(100vh - var(--header-height))",
          placeItems: "center",
        }}
      >
        <Card
          variant="warning"
          padding="lg"
          style={{
            width: "min(100%, 540px)",
          }}
        >
          <CardHeader>
            <div>
              <Badge
                variant="warning"
                icon={
                  <LockKeyhole
                    size={14}
                  />
                }
              >
                Acesso protegido
              </Badge>

              <h1
                style={{
                  margin:
                    "var(--space-4) 0 0",
                }}
              >
                Acesso não autorizado
              </h1>
            </div>

            <ShieldAlert size={28} />
          </CardHeader>

          <CardContent>
            <p
              style={{
                color:
                  "var(--text-secondary)",
                lineHeight:
                  "var(--line-relaxed)",
              }}
            >
              {notAuthenticated
                ? "É necessário iniciar uma sessão válida para acessar esta área."
                : "Seu perfil atual não possui a permissão necessária para acessar esta área do Auneron."}
            </p>

            {state?.from && (
              <p
                style={{
                  color:
                    "var(--text-muted)",
                  fontSize:
                    "var(--font-xs)",
                }}
              >
                Recurso solicitado:{" "}
                <code>{state.from}</code>
              </p>
            )}

            <Button
              onClick={() =>
                navigate("/", {
                  replace: true,
                })
              }
            >
              Voltar ao Dashboard
            </Button>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
