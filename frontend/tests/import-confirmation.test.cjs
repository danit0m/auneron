const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/lib/import-confirmation.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
function load(crypto) {
  const context = { exports: {}, crypto, Uint8Array };
  vm.runInNewContext(compiled, context);
  return context.exports;
}
const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
test('native intent IDs satisfy the backend UUID4 contract and are distinct', () => {
  const helper = load(webcrypto);
  const ids = Array.from({ length: 100 }, () => helper.createImportIntentId());
  assert.ok(ids.every(id => UUID4.test(id)));
  assert.equal(new Set(ids).size, 100);
});
test('crypto fallback preserves UUID version and variant', () => {
  const helper = load({ getRandomValues: bytes => bytes.fill(0) });
  assert.equal(helper.createImportIntentId(), '00000000-0000-4000-8000-000000000000');
});
test('confirmation uses the exact server preview fingerprints for CSV and XLSX', () => {
  const helper = load(webcrypto);
  const preview = { file_sha256: 'a'.repeat(64), content_sha256: 'b'.repeat(64) };
  for (const [name, format] of [['synthetic.csv', 'csv'], ['synthetic.XLSX', 'xlsx']]) {
    const result = JSON.parse(JSON.stringify(helper.confirmationParams(name, preview)));
    assert.deepEqual(result, { format, expected_file_sha256: preview.file_sha256, expected_content_sha256: preview.content_sha256 });
  }
});
test('missing crypto rejects confirmation ID creation without using weak randomness', () => {
  assert.throws(() => load(undefined).createImportIntentId(), /Não foi possível preparar/);
});
