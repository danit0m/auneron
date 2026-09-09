// Fatia 2C -- funcoes puras que decidem COMO agrupar contas por e-mail
// para deduplicar as chamadas de GET .../classification, e qual
// resposta assincrona ainda e valida para o ciclo de busca atual.
//
// Deliberadamente sem React, sem axios/fetch, sem DOM -- para poder ser
// verificado mecanicamente (node) sem depender de nenhum framework de
// teste. Ver frontend/scripts/verify-classification-grouping.ts.

export interface ClassificationGroupable {
  id: number;
  email: string | null;
}

export interface ClassificationGroup {
  key: string;
  email: string | null;
  representativeAccountId: number;
  accountIds: number[];
}

/**
 * Agrupa contas por e-mail para que cada e-mail unico gere no maximo
 * uma chamada GET /accounts/{id}/classification (a Fatia 2B garante que
 * qualquer account_id de um mesmo e-mail resolve para a mesma
 * classificacao).
 *
 * Contas com email=null NUNCA sao agrupadas entre si -- cada uma forma
 * seu proprio grupo isolado, para nao recriar o problema de identidade
 * que a Fatia 2B ja evita no backend.
 */
export function groupAccountsForClassification(
  accounts: readonly ClassificationGroupable[],
): ClassificationGroup[] {
  const groupsByEmail = new Map<string, ClassificationGroup>();
  const groups: ClassificationGroup[] = [];

  for (const account of accounts) {
    if (account.email === null) {
      groups.push({
        key: `null:${account.id}`,
        email: null,
        representativeAccountId: account.id,
        accountIds: [account.id],
      });
      continue;
    }

    const existente = groupsByEmail.get(account.email);

    if (existente) {
      existente.accountIds.push(account.id);
      continue;
    }

    const grupo: ClassificationGroup = {
      key: account.email,
      email: account.email,
      representativeAccountId: account.id,
      accountIds: [account.id],
    };

    groupsByEmail.set(account.email, grupo);
    groups.push(grupo);
  }

  return groups;
}

/**
 * Decisao pura de descarte de resposta obsoleta: uma resposta so deve
 * ser aplicada ao estado se o ciclo de busca em que ela foi disparada
 * ainda for o ciclo atual. A parte assincrona (useRef, chamadas HTTP)
 * fica no componente -- aqui so a regra de decisao.
 */
export function isCurrentClassificationCycle(
  responseCycle: number,
  currentCycle: number,
): boolean {
  return responseCycle === currentCycle;
}

/**
 * Remove do estado de classificacao (indexado por account_id) as
 * entradas cujo account_id nao esta mais na lista atual de contas --
 * evita estados orfaos quando a lista de clientes e recarregada e uma
 * conta deixa de existir nela.
 */
export function pruneOrphanClassificationState<T>(
  state: Readonly<Record<number, T>>,
  currentAccountIds: readonly number[],
): Record<number, T> {
  const permitidos = new Set(currentAccountIds);
  const podado: Record<number, T> = {};

  for (const [chave, valor] of Object.entries(state)) {
    const accountId = Number(chave);

    if (permitidos.has(accountId)) {
      podado[accountId] = valor;
    }
  }

  return podado;
}
