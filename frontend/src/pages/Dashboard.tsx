import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CircleDollarSign,
  Clock3,
  Users,
  WalletCards,
} from "lucide-react";
import { useEffect, useState } from "react";

import api from "../api/api";
import { Header } from "../components/layout/Header";
import type { DashboardData } from "../types/dashboard";

function formatarMoeda(valor: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  }).format(valor);
}

function formatarData(data: string): string {
  const [ano, mes, dia] = data.split("-");

  if (!ano || !mes || !dia) {
    return data;
  }

  return `${dia}/${mes}/${ano}`;
}

function obterClasseStatus(status: string): string {
  const statusNormalizado = status.toLowerCase();

  if (statusNormalizado === "pago") {
    return "status-badge status-paid";
  }

  if (statusNormalizado === "atrasado") {
    return "status-badge status-late";
  }

  return "status-badge status-open";
}

export function Dashboard() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState("");

  useEffect(() => {
    async function carregarDashboard() {
      try {
        setCarregando(true);
        setErro("");

        const response = await api.get<DashboardData>("/dashboard/");

        setDashboard(response.data);
      } catch (error) {
        console.error("Erro ao carregar o dashboard:", error);

        setErro(
          "Não foi possível conectar ao backend. Verifique se o FastAPI está em execução.",
        );
      } finally {
        setCarregando(false);
      }
    }

    void carregarDashboard();
  }, []);

  if (carregando) {
    return (
      <div className="page">
        <Header
          title="Dashboard"
          subtitle="Visão geral da sua operação financeira"
        />

        <div className="state-container">
          <div className="loading-spinner" />
          <p>Carregando informações financeiras...</p>
        </div>
      </div>
    );
  }

  if (erro || !dashboard) {
    return (
      <div className="page">
        <Header
          title="Dashboard"
          subtitle="Visão geral da sua operação financeira"
        />

        <div className="error-message">
          <AlertTriangle size={22} />
          <span>{erro || "Não foi possível carregar os dados."}</span>
        </div>
      </div>
    );
  }

  const { resumo, indicadores, ranking_clientes, alertas, vencimentos } =
    dashboard;

  return (
    <div className="page">
      <Header
        title="Dashboard"
        subtitle="Visão geral da sua operação financeira"
      />

      <section className="page-content">
        <div className="summary-grid">
          <article className="summary-card">
            <div className="summary-icon summary-icon-blue">
              <Users size={23} />
            </div>

            <div className="summary-card-content">
              <span>Total de clientes</span>
              <strong>{resumo.clientes_total}</strong>
              <small>Clientes cadastrados</small>
            </div>
          </article>

          <article className="summary-card">
            <div className="summary-icon summary-icon-purple">
              <WalletCards size={23} />
            </div>

            <div className="summary-card-content">
              <span>Faturamento total</span>
              <strong>{formatarMoeda(resumo.faturamento_total)}</strong>
              <small>Ticket médio: {formatarMoeda(indicadores.ticket_medio)}</small>
            </div>
          </article>

          <article className="summary-card">
            <div className="summary-icon summary-icon-green">
              <ArrowUpRight size={23} />
            </div>

            <div className="summary-card-content">
              <span>Total recebido</span>
              <strong>{formatarMoeda(resumo.recebido)}</strong>
              <small>{indicadores.taxa_recebimento} do faturamento</small>
            </div>
          </article>

          <article className="summary-card">
            <div className="summary-icon summary-icon-orange">
              <Clock3 size={23} />
            </div>

            <div className="summary-card-content">
              <span>Total pendente</span>
              <strong>{formatarMoeda(resumo.pendente)}</strong>
              <small>Valores ainda não recebidos</small>
            </div>
          </article>

          <article className="summary-card">
            <div className="summary-icon summary-icon-red">
              <ArrowDownRight size={23} />
            </div>

            <div className="summary-card-content">
              <span>Total atrasado</span>
              <strong>{formatarMoeda(resumo.atrasado)}</strong>
              <small>
                {indicadores.clientes_atrasados} clientes em atraso
              </small>
            </div>
          </article>
        </div>

        <div className="dashboard-grid">
          <section className="panel ranking-panel">
            <div className="panel-header">
              <div>
                <h2>Ranking de clientes</h2>
                <p>Maiores valores cadastrados</p>
              </div>

              <CircleDollarSign size={23} />
            </div>

            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>Posição</th>
                    <th>Cliente</th>
                    <th>Status</th>
                    <th>Valor</th>
                  </tr>
                </thead>

                <tbody>
                  {ranking_clientes.map((cliente, index) => (
                    <tr key={`${cliente.cliente}-${index}`}>
                      <td>
                        <span className="ranking-position">{index + 1}</span>
                      </td>

                      <td>
                        <strong>{cliente.cliente}</strong>
                      </td>

                      <td>
                        <span className={obterClasseStatus(cliente.status)}>
                          {cliente.status}
                        </span>
                      </td>

                      <td>{formatarMoeda(cliente.valor)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel alerts-panel">
            <div className="panel-header">
              <div>
                <h2>Alertas financeiros</h2>
                <p>Pagamentos que exigem atenção</p>
              </div>

              <AlertTriangle size={23} />
            </div>

            <div className="alerts-list">
              {alertas.length === 0 ? (
                <div className="empty-state">
                  <p>Nenhum alerta financeiro encontrado.</p>
                </div>
              ) : (
                alertas.map((alerta, index) => (
                  <article
                    className="alert-item"
                    key={`${alerta.cliente}-${index}`}
                  >
                    <div className="alert-item-icon">
                      <AlertTriangle size={19} />
                    </div>

                    <div className="alert-item-content">
                      <strong>{alerta.cliente}</strong>
                      <span>{alerta.mensagem}</span>
                    </div>

                    <strong className="alert-value">
                      {formatarMoeda(alerta.valor)}
                    </strong>
                  </article>
                ))
              )}
            </div>
          </section>
        </div>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Próximos vencimentos</h2>
              <p>Controle de pagamentos e recebimentos</p>
            </div>

            <Clock3 size={23} />
          </div>

          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Cliente</th>
                  <th>Vencimento</th>
                  <th>Status</th>
                  <th>Valor</th>
                </tr>
              </thead>

              <tbody>
                {vencimentos.map((vencimento, index) => (
                  <tr key={`${vencimento.cliente}-${index}`}>
                    <td>
                      <strong>{vencimento.cliente}</strong>
                    </td>

                    <td>{formatarData(vencimento.vencimento)}</td>

                    <td>
                      <span className={obterClasseStatus(vencimento.status)}>
                        {vencimento.status}
                      </span>
                    </td>

                    <td>{formatarMoeda(vencimento.valor)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </section>
    </div>
  );
}