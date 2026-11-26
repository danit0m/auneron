const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// No build, o Vite junta todos os CSS num arquivo so. Um bloco sem a chave de
// fechamento num arquivo engole todo o CSS que vem depois dele (incidente de
// 26/11/2026: um @media aberto em ExecutiveCenter.css deixou o site sem estilo).
// No modo de desenvolvimento cada arquivo e carregado separado e o erro nao aparece.

const SRC = path.join(__dirname, '../src');

function arquivosCss(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return arquivosCss(p);
    return e.name.endsWith('.css') ? [p] : [];
  });
}

// Percorre o CSS ignorando comentarios e textos entre aspas; devolve o primeiro problema.
function conferirChaves(css) {
  let nivel = 0;
  let linha = 1;
  const abertas = [];
  for (let i = 0; i < css.length; i++) {
    const c = css[i];
    if (c === '\n') linha++;
    if (c === '/' && css[i + 1] === '*') {
      const fim = css.indexOf('*/', i + 2);
      if (fim < 0) return `comentario aberto na linha ${linha}`;
      linha += (css.slice(i, fim).match(/\n/g) || []).length;
      i = fim + 1;
      continue;
    }
    if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < css.length && css[j] !== c) { if (css[j] === '\\') j++; j++; }
      i = j;
      continue;
    }
    if (c === '{') { nivel++; abertas.push(linha); }
    if (c === '}') {
      nivel--;
      abertas.pop();
      if (nivel < 0) return `chave } sem abertura na linha ${linha}`;
    }
  }
  return nivel === 0 ? null : `bloco aberto na linha ${abertas[abertas.length - 1]} sem }`;
}

test('o conferidor acha bloco aberto, chave sobrando e ignora comentarios e textos', () => {
  assert.equal(conferirChaves('a{b:c}@media (x){.d{e:f}}'), null);
  assert.match(conferirChaves('a{b:c}\n@media (x){\n.d{e:f}\n.g{h:i}'), /linha 2/);
  assert.match(conferirChaves('a{b:c}}'), /sem abertura/);
  assert.equal(conferirChaves('/* { */ a{content:"}"}'), null);
  assert.match(conferirChaves('a{b:c} /* aberto'), /comentario aberto/);
});

test('todo arquivo CSS de src tem as chaves balanceadas', () => {
  const arquivos = arquivosCss(SRC);
  assert.ok(arquivos.length > 20, `poucos arquivos CSS encontrados: ${arquivos.length}`);
  const problemas = arquivos
    .map((f) => [path.relative(SRC, f), conferirChaves(fs.readFileSync(f, 'utf8'))])
    .filter(([, p]) => p)
    .map(([f, p]) => `${f}: ${p}`);
  assert.deepEqual(problemas, []);
});
