import axios from "axios";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import api, { getApiErrorMessage, IDEMPOTENCY_KEY_HEADER } from "../api/api";
import { Header } from "../components/layout/Header";
import RegistrarPagamentoModal from "../components/recebiveis/RegistrarPagamentoModal";
import { AuneronBot, Badge, Button, Card, Spinner } from "../components/ui";
import { descreverPrazo, formatarMoeda } from "../lib/cockpit";
import { useAuth } from "../hooks/useAuth";
import {
  descreverPagamento,
  estadoPagamento,
  formatarDataBr,
  podeConcluirPagamento,
  type SituacaoPagamento,
} from "../lib/pagamento";
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
import {
  atrasoMedioPagamento,
  descreverAtrasoMedio,
  descreverPagosEmDia,
  descreverSemData,
  descreverTitulosPagos,
  pagosSemData,
  recebidoNoPeriodo,
} from "../lib/recebimento";
import type { Account } from "../types/account";
import type {
  ApprovalDetailsResponse,
  ApprovalListResponse,
  ApprovalRequestResponse,
} from "../types/approval";
import { getActionLabel, type NbaDecisionEvidenceResponse } from "../types/nba";

import "./Recebiveis.css";

const MAX_SIMULTANEAS = 6;

// GET /approvals não filtra por conta: busca pendentes e aprovadas (até
// 100 por página, em ordem crescente de id) e filtra no cliente.
const MAX_PAGINAS_APROVACOES = 10;

async function buscarAprovacoes(): Promise<ApprovalRequestResponse[]> {
  const itens: ApprovalRequestResponse[] = [];
  let cursor: number | null = null;

  for (let pagina = 0; pagina < MAX_PAGINAS_APROVACOES; pagina += 1) {
    const depois = cursor === null ? "" : `&after_id=${cursor}`;
    const response: { data: ApprovalListResponse } = await api.get<ApprovalListResponse>(
      `/approvals?status=pending&status=approved&limit=100${depois}`,
    );
    itens.push(...response.data.items);
    cursor = response.data.next_cursor;
    if (cursor === null) break;
  }

  return itens;
}

/** Mensagem em português da API: detail texto, ou detail.message dos erros de aprovação. */
function mensagemDaApi(error: unknown, padrao: string): string {
  if (axios.isAxiosError(error) && error.response?.data) {
    const detail: unknown = (error.response.data as { detail?: unknown }).detail;
    if (
      detail &&
      typeof detail === "object" &&
      "message" in detail &&
      typeof detail.message === "string"
    ) {
      return detail.message;
    }
  }
  return getApiErrorMessage(error, padrao);
}

interface AcaoPagamentoProps {
  conta: Account;
  situacao: SituacaoPagamento;
  podePedir: boolean;
  podeConcluir: boolean;
  concluindo: boolean;
  onPedir: (conta: Account) => void;
  onConcluir: (conta: Account, pedido: ApprovalRequestResponse) => void;
}

function AcaoPagamento({
  conta,
  situacao,
  podePedir,
  podeConcluir,
  concluindo,
  onPedir,
  onConcluir,
}: AcaoPagamentoProps) {
  const pago = descreverPagamento(conta);
  if (pago) return <span>{pago}</span>;

  const data = situacao.pedido?.effective_date;

  if (situacao.estado === "aguardando-aprovacao") {
    return (
      <div className="recebiveis-pagamento">
        <Badge variant="warning" dot>
          Aguardando aprovação
        </Badge>
        {data && <small>Pagamento em {formatarDataBr(data)}</small>}
      </div>
    );
  }

  if (situacao.estado === "aprovado-aguardando-conclusao" && situacao.pedido) {
    const pedido = situacao.pedido;
    return (
      <div className="recebiveis-pagamento">
        <Badge variant="success" dot>
          Aprovado — falta concluir
        </Badge>
        {data && <small>Pagamento em {formatarDataBr(data)}</small>}
        {podeConcluir && (
          <Button
            variant="primary"
            size="sm"
            loading={concluindo}
            aria-label={`Concluir pagamento de ${conta.cliente}`}
            onClick={() => onConcluir(conta, pedido)}
          >
            Concluir pagamento
          </Button>
        )}
      </div>
    );
  }

  if (situacao.estado === "sem-pedido" && podePedir) {
    return (
      <Button
        variant="secondary"
        size="sm"
        aria-label={`Registrar pagamento de ${conta.cliente}`}
        onClick={() => onPedir(conta)}
      >
        Registrar pagamento
      </Button>
    );
  }

  return <span className="recebiveis-muted">—</span>;
}

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
  const { user, hasPermission } = useAuth();
  const podePedir =
    hasPermission("skill:execute") && hasPermission("skill:execute_mutating");
  const podeDecidir = hasPermission("approval:decide");
  const podeVerAprovacoes = hasPermission("approval:read");

  const [contas, setContas] = useState<Account[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState("");
  const [aba, setAba] = useState<AbaChave>("todas");
  const [sugestoes, setSugestoes] = useState<Record<number, SugestaoEstado>>({});

  const [aprovacoes, setAprovacoes] = useState<ApprovalRequestResponse[]>([]);
  const [avisoAprovacoes, setAvisoAprovacoes] = useState("");
  // request_id -> usuário que decidiu (null = não foi possível saber).
  const [decisores, setDecisores] = useState<Record<number, number | null>>({});
  const [contaDoModal, setContaDoModal] = useState<Account | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [erroModal, setErroModal] = useState("");
  const [concluindoId, setConcluindoId] = useState<number | null>(null);
  const [feedback, setFeedback] = useState<{ tipo: "ok" | "erro"; texto: string } | null>(
    null,
  );

  const carregar = useCallback(async (silencioso = false) => {
    try {
      if (!silencioso) setCarregando(true);
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

  const carregarAprovacoes = useCallback(async () => {
    if (!podeVerAprovacoes) return;
    try {
      setAprovacoes(await buscarAprovacoes());
      setAvisoAprovacoes("");
    } catch (error) {
      console.error("Erro ao carregar pedidos de pagamento:", error);
      setAvisoAprovacoes(
        "Não foi possível carregar os pedidos de pagamento; o estado de cada título pode estar desatualizado.",
      );
    }
  }, [podeVerAprovacoes]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void carregarAprovacoes();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [carregarAprovacoes]);

  const itens = useMemo(() => classificarContas(contas, new Date()), [contas]);
  const resumo = useMemo(() => resumirFaixas(itens), [itens]);
  const recebido = useMemo(() => recebidoNoPeriodo(contas, new Date()), [contas]);
  const atrasoMedio = useMemo(() => atrasoMedioPagamento(contas), [contas]);
  const semData = useMemo(() => pagosSemData(contas), [contas]);
  const abas = useMemo(() => contarAbas(itens), [itens]);
  const fila = useMemo(() => filtrarFila(itens, aba), [itens, aba]);

  const situacoes = useMemo(() => {
    const agora = new Date();
    const mapa = new Map<number, SituacaoPagamento>();
    for (const item of itens) {
      mapa.set(item.conta.id, estadoPagamento(item.conta, aprovacoes, agora));
    }
    return mapa;
  }, [itens, aprovacoes]);

  // O botão "Concluir pagamento" exige usuário diferente do decisor; o
  // decisor só vem no detalhe do pedido, então busca só os aprovados.
  const idsSemDecisor = useMemo(() => {
    if (!podeDecidir) return [];
    const ids: number[] = [];
    for (const situacao of situacoes.values()) {
      const pedido = situacao.pedido;
      if (
        situacao.estado === "aprovado-aguardando-conclusao" &&
        pedido &&
        decisores[pedido.request_id] === undefined
      ) {
        ids.push(pedido.request_id);
      }
    }
    return ids;
  }, [situacoes, decisores, podeDecidir]);

  useEffect(() => {
    if (idsSemDecisor.length === 0) return;

    let cancelado = false;

    async function buscar() {
      for (const id of idsSemDecisor) {
        let decisor: number | null = null;
        try {
          const response = await api.get<ApprovalDetailsResponse>(`/approvals/${id}`);
          decisor = response.data.decision?.decided_by_user_id ?? null;
        } catch (error) {
          console.error("Erro ao carregar decisão do pedido:", id, error);
        }
        if (cancelado) return;
        setDecisores((anterior) => ({ ...anterior, [id]: decisor }));
      }
    }

    void buscar();

    return () => {
      cancelado = true;
    };
  }, [idsSemDecisor]);

  function abrirModal(conta: Account) {
    setErroModal("");
    setFeedback(null);
    setContaDoModal(conta);
  }

  const fecharModal = useCallback(() => {
    setContaDoModal(null);
    setErroModal("");
  }, []);

  async function enviarPedido(dados: { paidOn: string; idempotencyKey: string }) {
    if (!contaDoModal) return;

    setEnviando(true);
    setErroModal("");

    try {
      await api.post(
        `/accounts/${contaDoModal.id}/request-mark-paid`,
        { paid_on: dados.paidOn },
        { headers: { [IDEMPOTENCY_KEY_HEADER]: dados.idempotencyKey } },
      );
      setContaDoModal(null);
      setFeedback({
        tipo: "ok",
        texto:
          `Pedido de pagamento de ${formatarDataBr(dados.paidOn)} enviado para aprovação. ` +
          "O título continua em aberto até a aprovação e a conclusão.",
      });
      await carregarAprovacoes();
    } catch (error) {
      console.error("Erro ao enviar pedido de pagamento:", error);
      setErroModal(mensagemDaApi(error, "Não foi possível enviar o pedido de pagamento."));
    } finally {
      setEnviando(false);
    }
  }

  async function concluirPagamento(conta: Account, pedido: ApprovalRequestResponse) {
    setConcluindoId(conta.id);
    setFeedback(null);

    try {
      await api.post(`/accounts/${conta.id}/execute-mark-paid`, {
        approval_request_id: pedido.request_id,
        expected_status: conta.status,
      });
      setFeedback({
        tipo: "ok",
        texto:
          `Pagamento de ${conta.cliente} registrado` +
          (pedido.effective_date ? ` em ${formatarDataBr(pedido.effective_date)}.` : "."),
      });
      await Promise.all([carregar(true), carregarAprovacoes()]);
    } catch (error) {
      console.error("Erro ao concluir pagamento:", error);
      setFeedback({
        tipo: "erro",
        texto: mensagemDaApi(error, "Não foi possível concluir o pagamento."),
      });
    } finally {
      setConcluindoId(null);
    }
  }

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

  const temPagos = contas.some((c) => c.status === "pago");
  const cartaoRecebimentos = (
    <Card padding="lg">
      <section className="recebiveis-panel" aria-label="Recebimentos">
        <div>
          <h2>Recebimentos</h2>
          <p>Pagamentos com data registrada, em relação ao vencimento</p>
        </div>
        {recebido === null || atrasoMedio === null ? (
          <p className="recebiveis-muted">Ainda não há pagamentos com data registrada.</p>
        ) : (
          <dl className="recebiveis-indicadores">
            <div>
              <dt>Recebido nos últimos {recebido.dias} dias</dt>
              <dd>
                <strong>{formatarMoeda(recebido.valor)}</strong>
                <small>{descreverTitulosPagos(recebido.quantidade)}</small>
              </dd>
            </div>
            <div>
              <dt>Atraso médio no pagamento</dt>
              <dd>
                <strong>{descreverAtrasoMedio(atrasoMedio.mediaDias)}</strong>
              </dd>
            </div>
            <div>
              <dt>Pagos em dia</dt>
              <dd>
                <strong>{descreverPagosEmDia(atrasoMedio.emDia, atrasoMedio.total)}</strong>
                <small>pagos até o vencimento</small>
              </dd>
            </div>
          </dl>
        )}
        {semData && (
          <p className="recebiveis-nota">
            {descreverSemData(semData, formatarMoeda(semData.valor))}
          </p>
        )}
      </section>
    </Card>
  );

  if (itens.length === 0) {
    return (
      <div className="page">
        {header}
        <section className="page-content recebiveis">
          <Card padding="lg">
            <div className="recebiveis-empty">
              <AuneronBot mood="feliz" size="md" label="Nada em aberto" />
              <p>Nenhuma conta em aberto</p>
            </div>
          </Card>
          {temPagos && cartaoRecebimentos}
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

        {cartaoRecebimentos}

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

            {feedback && (
              <div
                className={
                  feedback.tipo === "ok"
                    ? "recebiveis-feedback recebiveis-feedback-ok"
                    : "recebiveis-feedback recebiveis-feedback-erro"
                }
                role={feedback.tipo === "ok" ? "status" : "alert"}
              >
                {feedback.tipo === "ok" ? (
                  <CheckCircle2 size={18} aria-hidden="true" />
                ) : (
                  <AlertTriangle size={18} aria-hidden="true" />
                )}
                <span>{feedback.texto}</span>
              </div>
            )}

            {avisoAprovacoes && (
              <p className="recebiveis-muted" role="status">
                {avisoAprovacoes}
              </p>
            )}

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
                      <th scope="col">Pagamento</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fila.map((item) => {
                      const sugestao = sugestoes[item.conta.id];
                      const situacao = situacoes.get(item.conta.id) ?? {
                        estado: "sem-pedido" as const,
                        pedido: null,
                      };
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
                          <td>
                            <AcaoPagamento
                              conta={item.conta}
                              situacao={situacao}
                              podePedir={podePedir}
                              podeConcluir={podeConcluirPagamento(
                                situacao,
                                podeDecidir,
                                user?.id,
                                situacao.pedido ? decisores[situacao.pedido.request_id] : null,
                              )}
                              concluindo={concluindoId === item.conta.id}
                              onPedir={abrirModal}
                              onConcluir={(conta, pedido) => void concluirPagamento(conta, pedido)}
                            />
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

      {contaDoModal && (
        <RegistrarPagamentoModal
          conta={contaDoModal}
          salvando={enviando}
          erro={erroModal}
          onClose={fecharModal}
          onSubmit={enviarPedido}
        />
      )}
    </div>
  );
}
