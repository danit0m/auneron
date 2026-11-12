import { AlertTriangle } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import api, { getApiErrorMessage } from "../api/api";
import { Header } from "../components/layout/Header";
import { AuneronBot, Badge, Button, Card, Spinner } from "../components/ui";
import { descreverPrazo, formatarMoeda } from "../lib/cockpit";
import {
  classificarContas,
  contarAbas,
  descreverAtraso,
  filtrarFila,
  resumirFaixas,
  type AbaChave,
  type ContaEmAberto,
  type FaixaChave,
} from "../lib/recebiveis";
import type { Account } from "../types/account";
import { getActionLabel, type NbaDecisionEvidenceResponse } from "../types/nba";

import "./Recebiveis.css";

const MAX_SIMULTANEAS = 6;

type SugestaoEstado =
  | { estado: "carregando" }
  | { estado: "ok"; texto: string }
  | { estado: "erro" };

const ABAS: ReadonlyArray<{ chave: AbaChave; rotulo: string }> = [
  { chave: "todas", rotulo: "Todas" },
  { chave: "vencidas", rotulo: "Vencidas" },
  { chave: "ate-3-dias", rotulo: "Vencem em até 3 dias" },
];

const COR_FAIXA: Record<FaixaChave, string> = {
  "a-vencer": "recebiveis-faixa-a-vencer",
  "1-30": "recebiveis-faixa-1-30",
  "31-60": "recebiveis-faixa-31-60",
  "61-90": "recebiveis-faixa-61-90",
  "mais-90": "recebiveis-faixa-mais-90",
};

const ROTULO_HUMOR: Record<string, string> = {
  critico: "Gravidade crítica",
  preocupado: "Gravidade média",
  atento: "Vence em breve",
  feliz: "Sem urgência",
};

function textoDaDecisao(nba: NbaDecisionEvidenceResponse): string {
  const primeira = nba.decision.selected_actions[0];
  return primeira ? getActionLabel(primeira) : "Nenhuma ação recomendada";
}

function formatarFaturas(n: number): string {
  return `${n} ${n === 1 ? "fatura" : "faturas"}`;
}

function Situacao({ item }: { item: ContaEmAberto }) {
  if (item.vencida) {
    return (
      <Badge variant={item.diasAtraso > 30 ? "danger" : "warning"} dot>
        {descreverAtraso(item.diasAtraso)}
      </Badge>
    );
  }
  return (
    <Badge variant="info" dot>
      {descreverPrazo(item.diasParaVencer)}
    </Badge>
  );
}

export default function Recebiveis() {
  const [contas, setContas] = useState<Account[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState("");
  const [aba, setAba] = useState<AbaChave>("todas");
  const [sugestoes, setSugestoes] = useState<Record<number, SugestaoEstado>>({});

  const carregar = useCallback(async () => {
    try {
      setCarregando(true);
      setErro("");
      const response = await api.get<Account[]>("/accounts/", { params: { limit: 200 } });
      setContas(response.data);
    } catch (error) {
      console.error("Erro ao carregar recebíveis:", error);
      setErro(getApiErrorMessage(error, "Não foi possível carregar os recebíveis."));
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void carregar();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [carregar]);

  const itens = useMemo(() => classificarContas(contas, new Date()), [contas]);
  const resumo = useMemo(() => resumirFaixas(itens), [itens]);
  const abas = useMemo(() => contarAbas(itens), [itens]);
  const fila = useMemo(() => filtrarFila(itens, aba), [itens, aba]);

  // Busca a decisão da política das contas vencidas, no máximo 6 por vez.
  useEffect(() => {
    const vencidas = itens.filter((i) => i.vencida).map((i) => i.conta);
    if (vencidas.length === 0) return;

    let cancelado = false;
    let proximo = 0;

    async function trabalhar() {
      while (!cancelado && proximo < vencidas.length) {
        const conta = vencidas[proximo++];
        let resultado: SugestaoEstado;
        try {
          const response = await api.get<NbaDecisionEvidenceResponse>(
            "/recommendations/next-best-action/" +
              `accounts/${conta.id}/episodes/${conta.vencimento}`,
          );
          resultado = { estado: "ok", texto: textoDaDecisao(response.data) };
        } catch (error) {
          console.error("Erro ao carregar recomendação da conta:", conta.id, error);
          resultado = { estado: "erro" };
        }
        if (cancelado) return;
        setSugestoes((anterior) => ({ ...anterior, [conta.id]: resultado }));
      }
    }

    const trabalhadores = Math.min(MAX_SIMULTANEAS, vencidas.length);
    for (let i = 0; i < trabalhadores; i += 1) {
      void trabalhar();
    }

    return () => {
      cancelado = true;
    };
  }, [itens]);

  const dataAtual = new Intl.DateTimeFormat("pt-BR", {
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(new Date());
  const header = <Header title="Recebíveis e cobrança" subtitle={dataAtual} />;

  if (carregando) {
    return (
      <div className="page">
        {header}
        <section className="page-content recebiveis-state">
          <Spinner size="lg" label="Carregando recebíveis" />
          <p>Carregando recebíveis...</p>
        </section>
      </div>
    );
  }

  if (erro) {
    return (
      <div className="page">
        {header}
        <section className="page-content">
          <Card variant="danger">
            <div className="recebiveis-error">
              <AlertTriangle size={22} aria-hidden="true" />
              <span>{erro}</span>
              <Button variant="secondary" size="sm" onClick={() => void carregar()}>
                Tentar novamente
              </Button>
            </div>
          </Card>
        </section>
      </div>
    );
  }

  if (itens.length === 0) {
    return (
      <div className="page">
        {header}
        <section className="page-content">
          <Card padding="lg">
            <div className="recebiveis-empty">
              <AuneronBot mood="feliz" size="md" label="Nada em aberto" />
              <p>Nenhuma conta em aberto</p>
            </div>
          </Card>
        </section>
      </div>
    );
  }

  return (
    <div className="page">
      {header}

      <section className="page-content recebiveis">
        <Card padding="lg">
          <div className="recebiveis-panel">
            <div>
              <h2>Onde está o dinheiro parado</h2>
              <p>Valores em aberto por faixa desde o vencimento</p>
            </div>
            <div className="recebiveis-total">
              <strong>{formatarMoeda(resumo.total)}</strong>
              <span>em aberto · {formatarFaturas(resumo.faturas)}</span>
            </div>
            <div
              className="recebiveis-stack"
              role="img"
              aria-label={resumo.faixas
                .map((f) => `${f.rotulo}: ${formatarMoeda(f.valor)}`)
                .join(", ")}
            >
              {resumo.faixas
                .filter((f) => f.valor > 0)
                .map((f) => (
                  <span
                    key={f.chave}
                    className={COR_FAIXA[f.chave]}
                    style={{ flexGrow: f.valor }}
                  />
                ))}
            </div>
            <ul className="recebiveis-legend">
              {resumo.faixas.map((f) => (
                <li key={f.chave}>
                  <span className={`recebiveis-swatch ${COR_FAIXA[f.chave]}`} aria-hidden="true" />
                  <span>{f.rotulo}</span>
                  <strong>{formatarMoeda(f.valor)}</strong>
                  <small>{formatarFaturas(f.faturas)}</small>
                </li>
              ))}
            </ul>
          </div>
        </Card>

        <Card padding="lg">
          <div className="recebiveis-panel">
            <div>
              <h2>Fila de cobrança</h2>
              <p>Contas vencidas e que vencem em até 3 dias</p>
            </div>

            <div className="recebiveis-tabs" role="tablist" aria-label="Filtro da fila">
              {ABAS.map((a) => (
                <button
                  key={a.chave}
                  type="button"
                  role="tab"
                  aria-selected={aba === a.chave}
                  className={
                    aba === a.chave ? "recebiveis-tab recebiveis-tab-ativa" : "recebiveis-tab"
                  }
                  onClick={() => setAba(a.chave)}
                >
                  {a.rotulo} <span className="recebiveis-tab-count">{abas[a.chave]}</span>
                </button>
              ))}
            </div>

            {fila.length === 0 ? (
              <p className="recebiveis-vazio-aba">Nenhuma conta nesta aba.</p>
            ) : (
              <div className="recebiveis-table-wrapper">
                <table className="recebiveis-table">
                  <thead>
                    <tr>
                      <th scope="col">Cliente</th>
                      <th scope="col">Valor</th>
                      <th scope="col">Situação</th>
                      <th scope="col">Próxima ação sugerida</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fila.map((item) => {
                      const sugestao = sugestoes[item.conta.id];
                      return (
                        <tr key={item.conta.id}>
                          <td>
                            <div className="recebiveis-cliente">
                              <AuneronBot
                                mood={item.mood}
                                size="sm"
                                label={ROTULO_HUMOR[item.mood]}
                              />
                              <strong>{item.conta.cliente}</strong>
                            </div>
                          </td>
                          <td className="recebiveis-valor">{formatarMoeda(item.valor)}</td>
                          <td>
                            <Situacao item={item} />
                          </td>
                          <td>
                            {!item.vencida ? (
                              <span>Nenhuma ação necessária</span>
                            ) : !sugestao || sugestao.estado === "carregando" ? (
                              <span className="recebiveis-muted">Carregando...</span>
                            ) : sugestao.estado === "erro" ? (
                              <span>—</span>
                            ) : (
                              <span>{sugestao.texto}</span>
                            )}
                            {item.vencida && (
                              <Link className="recebiveis-link" to="/recomendacoes">
                                Ver recomendação
                              </Link>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Card>
      </section>
    </div>
  );
}
