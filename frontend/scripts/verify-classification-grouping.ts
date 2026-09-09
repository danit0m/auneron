// Fatia 2C -- verificador mecanico ad-hoc dos invariantes de
// deduplicacao/agrupamento por e-mail e descarte de ciclo obsoleto.
//
// Nao e um framework de teste: e um script Node que importa as
// mesmas funcoes puras usadas em producao
// (src/lib/classification-grouping.ts) e falha com um erro se algum
// invariante quebrar. Compilado isoladamente (sem tocar no build da
// aplicacao) e executado via:
//
//   mkdir -p .verify-tmp
//   printf '{"type":"commonjs"}' > .verify-tmp/package.json
//   npx tsc --ignoreConfig --ignoreDeprecations 6.0 \
//     --target es2020 --module commonjs --moduleResolution node \
//     --types node --strict --outDir .verify-tmp --rootDir . \
//     src/lib/classification-grouping.ts \
//     scripts/verify-classification-grouping.ts
//   node .verify-tmp/scripts/verify-classification-grouping.js
//
// O package.json local com type=commonjs dentro de .verify-tmp
// existe só para sobrepor o "type": "module" do package.json real
// do projeto -- o .js compilado aqui é CommonJS, não ESM.
// .verify-tmp/ é descartável (não é commitado) e pode ser apagado
// apos a execucao.
//
// Sem dependencias novas: so usa "typescript" (ja existente como
// devDependency) e o modulo "assert" nativo do Node.

import assert from "node:assert/strict";

import {
  groupAccountsForClassification,
  isCurrentClassificationCycle,
  pruneOrphanClassificationState,
} from "../src/lib/classification-grouping";
import type { ClassificationGroup } from "../src/lib/classification-grouping";

function testarAgrupamentoPorEmailIgual(): void {
  const grupos = groupAccountsForClassification([
    { id: 1, email: "a@x.com" },
    { id: 2, email: "a@x.com" },
  ]);

  assert.equal(
    grupos.length,
    1,
    "duas contas com o mesmo email devem gerar exatamente 1 grupo",
  );

  assert.deepEqual(
    grupos[0]?.accountIds,
    [1, 2],
    "o grupo deve conter os dois account_ids",
  );

  assert.equal(
    grupos[0]?.representativeAccountId,
    1,
    "a conta representante deve ser a primeira encontrada",
  );
}

function testarEmailNuloNuncaAgrupado(): void {
  const grupos = groupAccountsForClassification([
    { id: 10, email: null },
    { id: 11, email: null },
  ]);

  assert.equal(
    grupos.length,
    2,
    "duas contas com email=null devem gerar 2 grupos independentes",
  );

  const idsRepresentantes = grupos
    .map((grupo: ClassificationGroup) => grupo.representativeAccountId)
    .sort((a: number, b: number) => a - b);

  assert.deepEqual(
    idsRepresentantes,
    [10, 11],
    "cada conta email=null deve ser sua propria representante",
  );

  for (const grupo of grupos) {
    assert.equal(
      grupo.accountIds.length,
      1,
      "grupo de conta email=null deve conter só a própria conta",
    );
  }
}

function testarAgrupamentoMisto(): void {
  const grupos = groupAccountsForClassification([
    { id: 1, email: "a@x.com" },
    { id: 2, email: null },
    { id: 3, email: "a@x.com" },
    { id: 4, email: null },
    { id: 5, email: "b@x.com" },
  ]);

  assert.equal(
    grupos.length,
    4,
    "1 grupo para a@x.com + 2 grupos isolados de email nulo + 1 grupo para b@x.com",
  );

  const grupoA = grupos.find(
    (grupo: ClassificationGroup) => grupo.email === "a@x.com",
  );

  assert.deepEqual(grupoA?.accountIds, [1, 3]);
}

function testarCicloAtual(): void {
  assert.equal(
    isCurrentClassificationCycle(3, 3),
    true,
    "resposta do ciclo atual deve ser aceita",
  );

  assert.equal(
    isCurrentClassificationCycle(2, 3),
    false,
    "resposta de um ciclo anterior deve ser descartada",
  );
}

function testarPodaDeOrfaos(): void {
  const estado = {
    1: "a",
    2: "b",
    3: "c",
  };

  const podado = pruneOrphanClassificationState(estado, [1, 3]);

  assert.deepEqual(
    podado,
    { 1: "a", 3: "c" },
    "conta que saiu da lista atual deve ser removida do estado",
  );
}

const testes: Array<[string, () => void]> = [
  ["agrupamento por email igual -> 1 grupo", testarAgrupamentoPorEmailIgual],
  ["email=null nunca agrupado entre si", testarEmailNuloNuncaAgrupado],
  ["agrupamento misto (email + null)", testarAgrupamentoMisto],
  ["ciclo atual aceito / ciclo antigo descartado", testarCicloAtual],
  ["poda de estado orfao por account_id ausente", testarPodaDeOrfaos],
];

let falhas = 0;

for (const [nome, teste] of testes) {
  try {
    teste();
    console.log(`PASS - ${nome}`);
  } catch (erro) {
    falhas += 1;
    console.error(`FAIL - ${nome}`);
    console.error(erro);
  }
}

if (falhas > 0) {
  console.error(`\n${falhas} de ${testes.length} verificacoes falharam.`);
  process.exit(1);
}

console.log(`\nTodas as ${testes.length} verificacoes passaram.`);
