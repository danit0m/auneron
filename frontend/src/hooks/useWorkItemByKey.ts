import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import api, { getApiErrorMessage } from "../api/api";
import type {
  WorkItemListResponse,
  WorkItemSummaryResponse,
} from "../types/nba";

interface UseWorkItemByKeyResult {
  workItem: WorkItemSummaryResponse | null;
  loading: boolean;
  error: string;
  refetch: () => void;
}

/**
 * Resolve deterministicamente UM WorkItem de escopo "account" pelo seu
 * work_key (ex.: "human_escalation:v1:{account_id}:{due_date}"),
 * reaproveitando o filtro exato de GET /work-items (VALUE-3.1) --
 * garantido no maximo 1 resultado pelo indice unico
 * uq_work_items_account_key, nunca uma busca ambigua.
 *
 * Escopo deliberadamente restrito: so resolve o WorkItem. Qualquer
 * dado dependente de domínio especifico (ex.: ApprovalRequest de
 * account.mark_overdue) fica fora do hook, encadeado pelo chamador.
 */
export function useWorkItemByKey(
  accountId: number,
  workKey: string,
  enabled: boolean = true,
): UseWorkItemByKeyResult {
  const [workItem, setWorkItem] =
    useState<WorkItemSummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Mesmo padrao de guard ja usado antes desta migracao -- descarta
  // respostas de uma chamada mais antiga se uma mais nova ja comecou
  // nesse meio-tempo (evita que uma resposta atrasada sobrescreva o
  // estado de uma expansao/conta mais recente).
  const requestIdRef = useRef(0);

  const fetchWorkItem = useCallback(async () => {
    if (!enabled) {
      return;
    }

    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError("");

    try {
      const response =
        await api.get<WorkItemListResponse>(
          "/work-items",
          {
            params: {
              scope_type: "account",
              account_id: accountId,
              work_key: workKey,
              limit: 1,
            },
          },
        );

      if (requestId !== requestIdRef.current) {
        return;
      }

      setWorkItem(
        response.data.items[0] ?? null,
      );
    } catch (err) {
      if (requestId !== requestIdRef.current) {
        return;
      }

      console.error(
        "Erro ao buscar WorkItem por work_key:",
        err,
      );
      setError(
        getApiErrorMessage(
          err,
          "Não foi possível carregar o estado do episódio.",
        ),
      );
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }, [accountId, workKey, enabled]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void fetchWorkItem();
    }, 0);

    return () =>
      window.clearTimeout(timeoutId);
  }, [fetchWorkItem]);

  return {
    workItem,
    loading,
    error,
    refetch: fetchWorkItem,
  };
}
