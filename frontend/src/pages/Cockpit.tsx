import { AlertTriangle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import api, {
  getApiErrorMessage,
} from "../api/api";
import { Header } from "../components/layout/Header";
import {
  AuneronBot,
  Badge,
  Button,
  Card,
  Spinner,
} from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import {
  descreverPrazo,
  formatarMoeda,
  montarCockpit,
  type ItemAtencao,
} from "../lib/cockpit";
import type { DashboardData } from "../types/dashboard";

import "./Cockpit.css";

function saudacao(agora: Date): string {
  const hora = agora.getHours();
  if (hora < 12) return "Bom dia";
  if (hora < 18) return "Boa tarde";
  return "Boa noite";
}

function porcentagem(fracao: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(fracao);
}

function ItemDaLista({ item }: { item: ItemAtencao }) {
  const emAtraso = item.tipo === "atraso";
  const mood = emAtraso
    ? "preocupado"
    : (item.dias ?? 99) <= 3
      ? "atento"
      : "feliz";

  return (
    <li className="cockpit-item">
      <AuneronBot mood={mood} size="sm" label={emAtraso ? "Em atraso" : "Vencimento próximo"} />
      <div className="cockpit-item-text">
        <strong>{item.cliente}</strong>
        <span>{formatarMoeda(item.valor)}</span>
      </div>
      <Badge variant={emAtraso ? "warning" : "info"} dot>
        {emAtraso ? "Em atraso" : descreverPrazo(item.dias ?? 0)}
      </Badge>
    </li>
  );
}

export function Cockpit() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [dados, setDados] = useState<DashboardData | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState("");

  useEffect(() => {
    async function carregar() {
      try {
        setCarregando(true);
        setErro("");
        const response = await api.get<DashboardData>("/dashboard/");
        setDados(response.data);
      } catch (error) {
        console.error("Erro ao carregar o cockpit:", error);
        setErro(getApiErrorMessage(error, "Não foi possível carregar o cockpit."));
      } finally {
        setCarregando(false);
      }
    }

    void carregar();
  }, []);

  const cockpit = useMemo(
    () => (dados ? montarCockpit(dados, new Date()) : null),
    [dados],
  );

  const primeiroNome = user?.name?.trim().split(/\s+/)[0];
  const header = (
    <Header title="Cockpit" subtitle="O que pede sua atenção hoje" />
  );

  if (carregando) {
    return (
      <div className="page">
        {header}
        <section className="page-content cockpit-state">
          <Spinner size="lg" label="Carregando o cockpit" />
          <p>Carregando informações financeiras...</p>
        </section>
      </div>
    );
  }

  if (erro || !cockpit) {
    return (
      <div className="page">
        {header}
        <section className="page-content">
          <Card variant="danger">
            <div className="cockpit-error">
              <AlertTriangle size={22} aria-hidden="true" />
              <span>{erro || "Não foi possível carregar os dados."}</span>
            </div>
          </Card>
        </section>
      </div>
    );
  }

  const temAtraso = cockpit.emAtraso > 0;
  const partes = [
    { chave: "recebido", rotulo: "Recebido", valor: cockpit.recebido },
    { chave: "a-vencer", rotulo: "A vencer", valor: cockpit.aVencer },
    { chave: "atraso", rotulo: "Em atraso", valor: cockpit.emAtraso },
  ];

  return (
    <div className="page">
      {header}

      <section className="page-content cockpit">
        <Card variant="brand" padding="lg">
          <div className="cockpit-hero">
            <AuneronBot mood={cockpit.mood} size="lg" label={cockpit.titulo} />
            <div className="cockpit-hero-text">
              <span className="cockpit-eyebrow">
                {saudacao(new Date())}{primeiroNome ? `, ${primeiroNome}` : ""}
              </span>
              <p className="cockpit-hero-title">{cockpit.titulo}</p>
              <p className="cockpit-hero-detail">{cockpit.detalhe}</p>
              <div className="cockpit-actions">
                <Button size="lg" onClick={() => navigate("/clientes")}>
                  {temAtraso ? "Ver contas em atraso" : "Ver clientes"}
                </Button>
              </div>
            </div>
          </div>
        </Card>

        <div className="cockpit-kpis">
          <Card>
            <div className="cockpit-kpi">
              <span>A receber</span>
              <strong>{formatarMoeda(cockpit.aReceber)}</strong>
              <small>{formatarMoeda(cockpit.aVencer)} ainda no prazo</small>
            </div>
          </Card>
          <Card variant={temAtraso ? "warning" : "default"}>
            <div className="cockpit-kpi">
              <span>Em atraso</span>
              <strong className={temAtraso ? "cockpit-kpi-warning" : undefined}>
                {formatarMoeda(cockpit.emAtraso)}
              </strong>
              <small>
                {cockpit.contasEmAtraso} {cockpit.contasEmAtraso === 1 ? "conta" : "contas"} ·{" "}
                {porcentagem(cockpit.parteEmAtraso)} do que há a receber
              </small>
            </div>
          </Card>
          <Card>
            <div className="cockpit-kpi">
              <span>Recebido</span>
              <strong>{formatarMoeda(cockpit.recebido)}</strong>
              <small>{porcentagem(cockpit.parteRecebida)} do faturamento</small>
            </div>
          </Card>
        </div>

        <div className="cockpit-columns">
          <Card padding="lg">
            <div className="cockpit-panel">
              <div>
                <h2>Como está o faturamento</h2>
                <p>{formatarMoeda(cockpit.faturamento)} cadastrados</p>
              </div>
              {cockpit.faturamento > 0 ? (
                <>
                  <div
                    className="cockpit-bar"
                    role="img"
                    aria-label={partes
                      .map((p) => `${p.rotulo}: ${formatarMoeda(p.valor)}`)
                      .join(", ")}
                  >
                    {partes
                      .filter((p) => p.valor > 0)
                      .map((p) => (
                        <span
                          key={p.chave}
                          className={`cockpit-bar-${p.chave}`}
                          style={{ flexGrow: p.valor }}
                        />
                      ))}
                  </div>
                  <ul className="cockpit-legend">
                    {partes.map((p) => (
                      <li key={p.chave}>
                        <span className={`cockpit-swatch cockpit-bar-${p.chave}`} aria-hidden="true" />
                        <span>{p.rotulo}</span>
                        <strong>{formatarMoeda(p.valor)}</strong>
                        <small>{porcentagem(p.valor / cockpit.faturamento)}</small>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="cockpit-empty">Nenhuma conta cadastrada ainda.</p>
              )}
            </div>
          </Card>

          <Card padding="lg">
            <div className="cockpit-panel">
              <div>
                <h2>Precisa da sua atenção</h2>
                <p>Contas em atraso e vencimentos dos próximos 7 dias</p>
              </div>
              {cockpit.itens.length > 0 ? (
                <ul className="cockpit-list">
                  {cockpit.itens.map((item, index) => (
                    <ItemDaLista key={`${item.tipo}-${item.cliente}-${index}`} item={item} />
                  ))}
                </ul>
              ) : (
                <div className="cockpit-empty-state">
                  <AuneronBot mood="feliz" size="md" label="Nada pede atenção agora" />
                  <p>Nada pede sua atenção agora.</p>
                </div>
              )}
              <div className="cockpit-panel-footer">
                <Button variant="ghost" size="sm" onClick={() => navigate("/clientes")}>
                  Ver todos os clientes
                </Button>
              </div>
            </div>
          </Card>
        </div>
      </section>
    </div>
  );
}
