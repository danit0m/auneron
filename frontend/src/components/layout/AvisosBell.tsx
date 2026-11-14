import { Bell } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import api, { fetchApprovalsPendingCount } from "../../api/api";
import { useAuth } from "../../hooks/useAuth";
import {
  contarPagamentosParaConcluir,
  MSG_SEM_AVISOS,
  montarAvisos,
  selecionarPedidosParaConcluir,
  totalAvisos,
  type ContagemConclusao,
  type EntradaAvisos,
} from "../../lib/avisos";
import { SKILL_MARK_PAID } from "../../lib/pagamento";
import type { Account } from "../../types/account";
import type { ApprovalDetailsResponse, ApprovalListResponse, ApprovalRequestResponse } from "../../types/approval";

// Mesmo teto de páginas da tela Recebíveis (GET /approvals só ordena por id asc).
const MAX_PAGINAS_APROVACOES = 10;
// Evita refazer todas as chamadas a cada troca de página ou abertura do painel.
const VALIDADE_CACHE_MS = 30_000;

interface Carga {
  aprovacoesPendentes: number | null;
  pagamentos: ContagemConclusao | null;
}

let cache: { usuarioId: number; em: number; carga: Carga } | null = null;

async function buscarPedidosAprovados(): Promise<ApprovalRequestResponse[]> {
  const itens: ApprovalRequestResponse[] = [];
  let cursor: number | null = null;

  for (let pagina = 0; pagina < MAX_PAGINAS_APROVACOES; pagina += 1) {
    const depois = cursor === null ? "" : `&after_id=${cursor}`;
    const response: { data: ApprovalListResponse } = await api.get<ApprovalListResponse>(
      `/approvals?status=approved&limit=100${depois}`,
    );
    itens.push(...response.data.items);
    cursor = response.data.next_cursor;
    if (cursor === null) break;
  }

  return itens.filter((a) => a.skill_key === SKILL_MARK_PAID);
}

async function carregarPagamentos(usuarioId: number): Promise<ContagemConclusao | null> {
  try {
    const [aprovados, contasResp] = await Promise.all([
      buscarPedidosAprovados(),
      api.get<Account[]>("/accounts/", { params: { limit: 200 } }),
    ]);
    const contas = contasResp.data;
    const agora = new Date();
    const { pedidos, truncado } = selecionarPedidosParaConcluir(aprovados, contas, agora);

    const decisores: Record<number, number | null> = {};
    for (const pedido of pedidos) {
      const resp = await api.get<ApprovalDetailsResponse>(`/approvals/${pedido.request_id}`);
      decisores[pedido.request_id] = resp.data.decision?.decided_by_user_id ?? null;
    }

    return {
      total: contarPagamentosParaConcluir(pedidos, contas, decisores, true, usuarioId, agora),
      truncado,
    };
  } catch (error) {
    // Fail-closed: sem dado confiável, o item some e não há contador falso.
    console.error("Erro ao carregar avisos de pagamentos:", error);
    return null;
  }
}

async function carregarAprovacoes(): Promise<number | null> {
  try {
    return await fetchApprovalsPendingCount();
  } catch (error) {
    console.error("Erro ao carregar avisos de aprovações:", error);
    return null;
  }
}

export function AvisosBell() {
  const { user, hasPermission } = useAuth();
  const podeVerAprovacoes = hasPermission("approval:read");
  const podeDecidir = hasPermission("approval:decide");
  const usuarioId = user?.id ?? null;

  const [carga, setCarga] = useState<Carga>({ aprovacoesPendentes: null, pagamentos: null });
  const [aberto, setAberto] = useState(false);
  const areaRef = useRef<HTMLDivElement>(null);
  const botaoRef = useRef<HTMLButtonElement>(null);

  const carregar = useCallback(
    async (forcar: boolean) => {
      if (!podeVerAprovacoes || usuarioId === null) return;
      if (!forcar && cache && cache.usuarioId === usuarioId && Date.now() - cache.em < VALIDADE_CACHE_MS) {
        setCarga(cache.carga);
        return;
      }
      const [aprovacoesPendentes, pagamentos] = await Promise.all([
        carregarAprovacoes(),
        podeDecidir ? carregarPagamentos(usuarioId) : Promise.resolve(null),
      ]);
      const nova = { aprovacoesPendentes, pagamentos };
      cache = { usuarioId, em: Date.now(), carga: nova };
      setCarga(nova);
    },
    [podeVerAprovacoes, podeDecidir, usuarioId],
  );

  useEffect(() => {
    const id = window.setTimeout(() => void carregar(false), 0);
    return () => window.clearTimeout(id);
  }, [carregar]);

  useEffect(() => {
    if (!aberto) return;

    function aoClicar(evento: MouseEvent) {
      if (areaRef.current && !areaRef.current.contains(evento.target as Node)) setAberto(false);
    }
    function aoTecla(evento: KeyboardEvent) {
      if (evento.key === "Escape") {
        setAberto(false);
        botaoRef.current?.focus();
      }
    }

    document.addEventListener("mousedown", aoClicar);
    document.addEventListener("keydown", aoTecla);
    return () => {
      document.removeEventListener("mousedown", aoClicar);
      document.removeEventListener("keydown", aoTecla);
    };
  }, [aberto]);

  const entrada: EntradaAvisos = { podeVerAprovacoes, podeDecidir, ...carga };
  const itens = montarAvisos(entrada);
  const total = totalAvisos(itens, entrada);

  function alternar() {
    const abrir = !aberto;
    setAberto(abrir);
    if (abrir) void carregar(false);
  }

  return (
    <div className="avisos" ref={areaRef}>
      <button
        type="button"
        ref={botaoRef}
        className="header-icon-button avisos-botao"
        aria-label={total ? `Notificações: ${total} ${total === 1 ? "aviso" : "avisos"}` : "Notificações"}
        aria-expanded={aberto}
        aria-controls="avisos-painel"
        onClick={alternar}
      >
        <Bell size={20} />
        {total !== null && <span className="avisos-contador">{total > 99 ? "99+" : total}</span>}
      </button>

      {aberto && (
        <div id="avisos-painel" className="avisos-painel" role="region" aria-label="Avisos">
          {itens.length === 0 ? (
            <p className="avisos-vazio">{MSG_SEM_AVISOS}</p>
          ) : (
            <ul className="avisos-lista">
              {itens.map((item) => (
                <li key={item.chave}>
                  <Link to={item.destino} onClick={() => setAberto(false)}>
                    {item.texto}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
