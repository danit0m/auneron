# Consolidação das regras de negócio

## Primeira etapa implementada

O motor de decisões e os quatro agentes legados passam a consumir
`app.core.receivable_analysis.evaluate_receivable_analysis`, que delega a
observação de vencimento para `evaluate_receivable_lifecycle` e usa
`business_today`.

| Fato confirmado | Interpretação para análise |
| --- | --- |
| Status pago | Pago, sem atraso ativo |
| Status aberto ou atrasado e vencimento anterior ao dia de negócio | Atraso operacional observado |
| Vencimento hoje ou futuro | Sem atraso operacional |
| Status desconhecido, data inválida ou ausente | Não produzir análise por esses agentes |
| Dados inválidos no motor de decisões | Decisão sem agentes e sem confiança atribuída |

A observação operacional não escreve Account.status. O payload original também
permanece preservado para rastreabilidade. A regra de 14 dias para proximidade do
vencimento e os estados de atraso do lifecycle continuam com sua política própria.

FinanceAgent, RiskAgent, AnalyticsAgent e NotificationAgent usam os mesmos fatos
operacionais. Limiares de valor, pontuação de risco e segmentação comercial
continuam sendo políticas distintas. Essa etapa não escolhe um limiar único
entre critérios que respondem perguntas diferentes.

## Diferenças que impedem declarar toda a unificação concluída

| Tema | Runtime atual | Avaliação offline da IA |
| --- | --- | --- |
| Valor versus saldo | Account.valor é o valor do título e tem restrição positiva | balance_cents representa saldo confirmado |
| Bloqueio | Account não tem campo de hold | hold explícito impede proposta |
| Carência | Lifecycle observa vencimento anterior ao dia de negócio | Cada caso informa grace_days |
| Conta já vencida | Status persistido e situação operacional são separados | status e overdue_since fazem parte do contrato sintético |
| Data de referência | Parte dos corredores ainda usa date.today | Avaliação usa as_of fixado |
| Limite de 30 dias | Políticas específicas de risco/escalonamento | Fronteira de capacidade da tarefa avaliada |

Saldo ausente não equivale a zero. Account.valor não deve ser usado como
substituto de saldo em aberto. A carência dos cenários sintéticos não é uma
decisão de carência padrão para todas as contas do runtime.

A decisão de saldo zero permanece válida. Para aplicá-la de ponta a ponta,
é necessário definir a origem e a representação do saldo confirmado, com
identidade e atualização do título. Bloqueio e carência também precisam de
representação explícita antes de virar gates do runtime.

## Próximos consumidores

Os corredores de sugestão, materialização, scanner e execução precisam consumir
uma elegibilidade compartilhada, mantendo a revalidação dentro da transação de
execução. A alteração precisa cobrir os motivos de recusa, seus contratos e
testes PostgreSQL. A autoridade de aprovação e a proteção contra duplicidade
continuam sendo responsabilidades do executor governado.

O conjunto independente, seus rótulos, hashes e prompts congelados não foram
usados para ajustar essa implementação.

## Verificação desta etapa

Teste unitário `test_shared_receivable_analysis.py` cobre a política compartilhada,
os quatro handlers reais com persistência simulada, o contexto do motor de
decisões e a preservação do payload. Esses testes não comprovam execução com
PostgreSQL, integração ao runtime implantado ou qualidade do modelo.
