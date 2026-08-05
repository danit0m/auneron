import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  Info,
  KeyRound,
  LockKeyhole,
  Moon,
  Palette,
  Search,
  ShieldCheck,
  Sparkles,
  Sun,
  Trash2,
} from "lucide-react";
import {
  useEffect,
  useState,
} from "react";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  Input,
  Spinner,
} from "../../components/ui";
import {
  useTheme,
} from "../../hooks/useTheme";

import "../../styles/ui-showcase.css";

interface TokenItem {
  name: string;
  variable: string;
  description: string;
}

const colorTokens: TokenItem[] = [
  {
    name: "Background",
    variable: "--background",
    description: "Plano de fundo principal da aplicação.",
  },
  {
    name: "Surface",
    variable: "--surface",
    description: "Superfície padrão para cards e painéis.",
  },
  {
    name: "Primary",
    variable: "--color-primary-600",
    description: "Ação principal e foco da interface.",
  },
  {
    name: "Brand",
    variable: "--color-brand-600",
    description: "Identidade visual do Auneron.",
  },
  {
    name: "Success",
    variable: "--color-success-600",
    description: "Estados positivos e concluídos.",
  },
  {
    name: "Warning",
    variable: "--color-warning-600",
    description: "Atenção e prioridade moderada.",
  },
  {
    name: "Danger",
    variable: "--color-danger-600",
    description: "Ações críticas e estados de risco.",
  },
  {
    name: "Info",
    variable: "--color-info-600",
    description: "Informações contextuais.",
  },
];

const spacingTokens = [
  "--space-1",
  "--space-2",
  "--space-3",
  "--space-4",
  "--space-5",
  "--space-6",
  "--space-8",
  "--space-12",
];

const radiusTokens = [
  "--radius-sm",
  "--radius-md",
  "--radius-lg",
  "--radius-xl",
  "--radius-2xl",
  "--radius-full",
];

const shadowTokens = [
  "--shadow-xs",
  "--shadow-sm",
  "--shadow-md",
  "--shadow-lg",
  "--shadow-xl",
];

function getCssVariable(
  variable: string,
): string {
  if (typeof window === "undefined") {
    return "";
  }

  return getComputedStyle(
    document.documentElement,
  )
    .getPropertyValue(variable)
    .trim();
}

export default function UIShowcase() {
  const {
    preference,
    resolvedTheme,
    isDark,
    setTheme,
    toggleTheme,
  } = useTheme();

  const [
    inputValue,
    setInputValue,
  ] = useState("");

  const [
    loadingExample,
    setLoadingExample,
  ] = useState(false);

  const [
    tokenValues,
    setTokenValues,
  ] = useState(() =>
    colorTokens.map((token) => ({
      ...token,
      value: "",
    })),
  );

  useEffect(() => {
    const frameId =
      window.requestAnimationFrame(() => {
        setTokenValues(
          colorTokens.map((token) => ({
            ...token,
            value: getCssVariable(
              token.variable,
            ),
          })),
        );
      });

    return () => {
      window.cancelAnimationFrame(
        frameId,
      );
    };
  }, [resolvedTheme]);

  function testarLoading() {
    setLoadingExample(true);

    window.setTimeout(() => {
      setLoadingExample(false);
    }, 1200);
  }

  return (
    <div className="ui-showcase-page">
      <section className="ui-showcase-hero">
        <div className="ui-showcase-hero-main">
          <div className="ui-showcase-hero-icon">
            <Palette size={30} />
          </div>

          <div>
            <span className="ui-showcase-eyebrow">
              Administration · Design System
            </span>

            <h1>
              Auneron UI Showcase
            </h1>

            <p>
              Laboratório oficial para validar componentes,
              temas, tokens, estados e padrões visuais da
              plataforma.
            </p>
          </div>
        </div>

        <div className="ui-showcase-hero-meta">
          <div>
            <span>Design System</span>
            <strong>Enterprise 1.0</strong>
          </div>

          <div>
            <span>Sprint</span>
            <strong>8.1</strong>
          </div>

          <div>
            <span>Tema atual</span>
            <strong>
              {resolvedTheme === "dark"
                ? "Escuro"
                : "Claro"}
            </strong>
          </div>
        </div>
      </section>

      <section className="ui-showcase-security-notice">
        <LockKeyhole size={20} />

        <div>
          <strong>
            Área administrativa protegida
          </strong>

          <span>
            Esta página deverá ficar disponível somente para
            usuários com credencial elevada.
          </span>
        </div>

        <Badge
          variant="warning"
          icon={<KeyRound size={14} />}
        >
          Elevated Access
        </Badge>
      </section>

      <section className="ui-showcase-theme-panel">
        <div>
          <span className="ui-showcase-section-eyebrow">
            Theme Foundation
          </span>

          <h2>Controle de tema</h2>

          <p>
            Valide os componentes nos modos claro, escuro ou
            conforme a preferência do sistema operacional.
          </p>
        </div>

        <div className="ui-showcase-theme-actions">
          <Button
            variant={
              preference === "light"
                ? "primary"
                : "secondary"
            }
            startIcon={<Sun size={17} />}
            onClick={() => setTheme("light")}
          >
            Claro
          </Button>

          <Button
            variant={
              preference === "dark"
                ? "primary"
                : "secondary"
            }
            startIcon={<Moon size={17} />}
            onClick={() => setTheme("dark")}
          >
            Escuro
          </Button>

          <Button
            variant={
              preference === "system"
                ? "primary"
                : "outline"
            }
            startIcon={<Activity size={17} />}
            onClick={() => setTheme("system")}
          >
            Sistema
          </Button>

          <Button
            variant="ghost"
            onClick={toggleTheme}
          >
            Alternar tema
          </Button>
        </div>
      </section>

      <section className="ui-showcase-section">
        <div className="ui-showcase-section-header">
          <div>
            <span className="ui-showcase-section-eyebrow">
              Componentes fundamentais
            </span>

            <h2>Buttons</h2>

            <p>
              Variantes, tamanhos, estados e combinações de
              ícones.
            </p>
          </div>

          <Badge variant="brand">
            5 variantes
          </Badge>
        </div>

        <Card padding="lg">
          <CardContent>
            <div className="ui-showcase-component-group">
              <h3>Variantes</h3>

              <div className="ui-showcase-inline">
                <Button
                  startIcon={<Sparkles size={17} />}
                >
                  Primary
                </Button>

                <Button variant="secondary">
                  Secondary
                </Button>

                <Button variant="outline">
                  Outline
                </Button>

                <Button variant="ghost">
                  Ghost
                </Button>

                <Button
                  variant="danger"
                  startIcon={<Trash2 size={17} />}
                >
                  Danger
                </Button>
              </div>
            </div>

            <div className="ui-showcase-component-group">
              <h3>Tamanhos e estados</h3>

              <div className="ui-showcase-inline ui-showcase-align-end">
                <Button size="sm">
                  Small
                </Button>

                <Button size="md">
                  Medium
                </Button>

                <Button size="lg">
                  Large
                </Button>

                <Button
                  loading={loadingExample}
                  onClick={testarLoading}
                >
                  {loadingExample
                    ? "Processando"
                    : "Testar loading"}
                </Button>

                <Button disabled>
                  Disabled
                </Button>

                <Button
                  variant="outline"
                  endIcon={<ChevronRight size={17} />}
                >
                  Com ícone
                </Button>
              </div>
            </div>

            <div className="ui-showcase-component-group">
              <h3>Largura total</h3>

              <Button
                fullWidth
                startIcon={<ShieldCheck size={17} />}
              >
                Ação principal em largura total
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="ui-showcase-section">
        <div className="ui-showcase-section-header">
          <div>
            <span className="ui-showcase-section-eyebrow">
              Superfícies e hierarquia
            </span>

            <h2>Cards</h2>

            <p>
              Cards reutilizáveis para cenários executivos,
              operacionais e críticos.
            </p>
          </div>
        </div>

        <div className="ui-showcase-card-grid">
          <Card>
            <CardHeader>
              <div>
                <span className="ui-showcase-card-eyebrow">
                  Default
                </span>
                <h3>Card padrão</h3>
              </div>

              <Badge>Neutral</Badge>
            </CardHeader>

            <CardContent>
              <p>
                Superfície principal para conteúdos gerais.
              </p>
            </CardContent>

            <CardFooter>
              <Button size="sm" variant="ghost">
                Ver detalhes
              </Button>
            </CardFooter>
          </Card>

          <Card variant="brand">
            <CardHeader>
              <div>
                <span className="ui-showcase-card-eyebrow">
                  Brand
                </span>
                <h3>Inteligência executiva</h3>
              </div>

              <Sparkles size={20} />
            </CardHeader>

            <CardContent>
              <p>
                Destaque visual alinhado à identidade do
                Auneron.
              </p>
            </CardContent>
          </Card>

          <Card variant="elevated">
            <CardHeader>
              <div>
                <span className="ui-showcase-card-eyebrow">
                  Elevated
                </span>
                <h3>Painel elevado</h3>
              </div>
            </CardHeader>

            <CardContent>
              <p>
                Indicado para elementos flutuantes e painéis
                prioritários.
              </p>
            </CardContent>
          </Card>

          <Card variant="success">
            <CardHeader>
              <h3>Processo concluído</h3>
              <CheckCircle2 size={20} />
            </CardHeader>

            <CardContent>
              <p>
                Usado para resultados positivos e confirmações.
              </p>
            </CardContent>
          </Card>

          <Card variant="warning">
            <CardHeader>
              <h3>Atenção necessária</h3>
              <AlertTriangle size={20} />
            </CardHeader>

            <CardContent>
              <p>
                Usado para riscos moderados e acompanhamento.
              </p>
            </CardContent>
          </Card>

          <Card variant="danger">
            <CardHeader>
              <h3>Ação crítica</h3>
              <Trash2 size={20} />
            </CardHeader>

            <CardContent>
              <p>
                Usado para situações críticas ou destrutivas.
              </p>
            </CardContent>
          </Card>
        </div>
      </section>

      <section className="ui-showcase-section">
        <div className="ui-showcase-section-header">
          <div>
            <span className="ui-showcase-section-eyebrow">
              Estados semânticos
            </span>

            <h2>Badges</h2>

            <p>
              Indicadores compactos para prioridade, status e
              classificação.
            </p>
          </div>
        </div>

        <Card>
          <CardContent>
            <div className="ui-showcase-inline">
              <Badge dot>
                Neutral
              </Badge>

              <Badge
                variant="brand"
                icon={<Sparkles size={14} />}
              >
                Brand
              </Badge>

              <Badge
                variant="info"
                icon={<Info size={14} />}
              >
                Informativa
              </Badge>

              <Badge
                variant="success"
                icon={<CheckCircle2 size={14} />}
              >
                Concluído
              </Badge>

              <Badge
                variant="warning"
                icon={<AlertTriangle size={14} />}
              >
                Atenção
              </Badge>

              <Badge
                variant="danger"
                icon={<Trash2 size={14} />}
              >
                Crítico
              </Badge>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="ui-showcase-section">
        <div className="ui-showcase-section-header">
          <div>
            <span className="ui-showcase-section-eyebrow">
              Entrada de dados
            </span>

            <h2>Inputs</h2>

            <p>
              Campos com label, ícones, ajuda, erro e estado
              desabilitado.
            </p>
          </div>
        </div>

        <Card padding="lg">
          <CardContent>
            <div className="ui-showcase-input-grid">
              <Input
                label="Pesquisa"
                placeholder="Pesquisar no Auneron..."
                startIcon={<Search size={17} />}
                value={inputValue}
                onChange={(event) =>
                  setInputValue(event.target.value)
                }
              />

              <Input
                label="Cliente"
                placeholder="Nome da empresa"
                helperText="Digite a razão social ou nome fantasia."
                required
              />

              <Input
                label="Campo com erro"
                defaultValue="Valor inválido"
                error="Revise o conteúdo informado."
                startIcon={<AlertTriangle size={17} />}
              />

              <Input
                label="Campo desabilitado"
                defaultValue="Acesso restrito"
                disabled
                endIcon={<LockKeyhole size={17} />}
              />
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="ui-showcase-section">
        <div className="ui-showcase-section-header">
          <div>
            <span className="ui-showcase-section-eyebrow">
              Feedback de carregamento
            </span>

            <h2>Spinners</h2>

            <p>
              Indicadores oficiais de carregamento da
              plataforma.
            </p>
          </div>
        </div>

        <Card>
          <CardContent>
            <div className="ui-showcase-spinner-grid">
              <div>
                <Spinner size="sm" />
                <span>Small</span>
              </div>

              <div>
                <Spinner size="md" />
                <span>Medium</span>
              </div>

              <div>
                <Spinner size="lg" />
                <span>Large</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="ui-showcase-section">
        <div className="ui-showcase-section-header">
          <div>
            <span className="ui-showcase-section-eyebrow">
              Design tokens
            </span>

            <h2>Tokens Viewer</h2>

            <p>
              Referência visual das variáveis utilizadas pelos
              componentes.
            </p>
          </div>

          <Badge
            variant="info"
            icon={<CircleHelp size={14} />}
          >
            Tema: {resolvedTheme}
          </Badge>
        </div>

        <div className="ui-showcase-token-grid">
          {tokenValues.map((token) => (
            <Card
              key={token.variable}
              padding="sm"
            >
              <div className="ui-showcase-token-color">
                <div
                  style={{
                    background:
                      `var(${token.variable})`,
                  }}
                />

                <div>
                  <strong>{token.name}</strong>
                  <code>{token.variable}</code>
                  <span>{token.description}</span>
                  <small>{token.value}</small>
                </div>
              </div>
            </Card>
          ))}
        </div>

        <Card
          className="ui-showcase-token-reference"
          padding="lg"
        >
          <CardHeader>
            <div>
              <span className="ui-showcase-card-eyebrow">
                Foundation
              </span>

              <h3>
                Espaçamento, bordas e sombras
              </h3>
            </div>
          </CardHeader>

          <CardContent>
            <div className="ui-showcase-token-reference-grid">
              <div>
                <h4>Spacing</h4>

                <div className="ui-showcase-spacing-list">
                  {spacingTokens.map((token) => (
                    <div key={token}>
                      <span>{token}</span>

                      <div
                        style={{
                          width: `var(${token})`,
                        }}
                      />
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h4>Radius</h4>

                <div className="ui-showcase-radius-list">
                  {radiusTokens.map((token) => (
                    <div
                      key={token}
                      style={{
                        borderRadius:
                          `var(${token})`,
                      }}
                    >
                      {token}
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h4>Shadows</h4>

                <div className="ui-showcase-shadow-list">
                  {shadowTokens.map((token) => (
                    <div
                      key={token}
                      style={{
                        boxShadow:
                          `var(${token})`,
                      }}
                    >
                      {token}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      <footer className="ui-showcase-footer">
        <ShieldCheck size={18} />

        <span>
          Auneron Enterprise Design Laboratory · Commit 4
        </span>

        <Badge variant={isDark ? "brand" : "info"}>
          {isDark ? "Dark Mode" : "Light Mode"}
        </Badge>
      </footer>
    </div>
  );
}