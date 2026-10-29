import { useEffect, useRef, useState } from "react";
import { FileUp } from "lucide-react";
import axios from "axios";
import { Header } from "../components/layout/Header";
import api, { getApiErrorMessage } from "../api/api";
import { confirmationParams, createImportIntentId } from "../lib/import-confirmation";
import "./Upload.css";

type Preview = {
  file_sha256: string;
  content_sha256: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  valid_total: string;
  rows_truncated: boolean;
  rows: { row_number: number; valid: boolean; issues: { field: string | null; code: string; message: string }[] }[];
};
type Receipt = { replayed: boolean; record_count: number; total: string; account_references: number[] };
const MAX_BYTES = 5 * 1024 * 1024;
const currency = (amount: string) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(amount));
const records = (count: number) => `${count} ${count === 1 ? "registro" : "registros"}`;

export function Upload() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [approved, setApproved] = useState(false);
  const [available, setAvailable] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const request = useRef<AbortController | null>(null);
  const version = useRef(0);
  const key = useRef<string | null>(null);
  const confirmingRef = useRef(false);
  const locked = confirming || uncertain;

  useEffect(() => {
    const controller = new AbortController();
    api.get<{ confirmation_available: boolean }>("/imports/capabilities", { signal: controller.signal })
      .then((response) => setAvailable(response.data.confirmation_available === true))
      .catch((failure) => {
        setAvailable(false);
        // 401/403 is an access problem, not a disabled environment.
        const status = axios.isAxiosError(failure) ? failure.response?.status : undefined;
        setAccessDenied(status === 401 || status === 403);
      });
    return () => { controller.abort(); request.current?.abort(); };
  }, []);

  function selectFile(next: File | null) {
    if (confirmingRef.current || uncertain) return;
    version.current += 1;
    request.current?.abort();
    key.current = null;
    setBusy(false);
    setPreview(null);
    setReceipt(null);
    setApproved(false);
    setError("");
    setFile(null);
    if (!next) return;
    if (!/\.(csv|xlsx)$/i.test(next.name)) setError("Selecione um arquivo CSV ou XLSX.");
    else if (next.size === 0 || next.size > MAX_BYTES) setError("O arquivo deve conter dados e ter no máximo 5 MiB.");
    else setFile(next);
  }

  async function loadPreview() {
    if (!file || busy || confirmingRef.current || uncertain) return;
    const current = ++version.current;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    key.current = null;
    setBusy(true);
    setPreview(null);
    setReceipt(null);
    setApproved(false);
    setError("");
    try {
      const response = await api.post<Preview>("/imports/preview", file, {
        params: { format: file.name.toLowerCase().endsWith(".xlsx") ? "xlsx" : "csv", include_values: false },
        headers: { "Content-Type": "application/octet-stream" }, signal: controller.signal,
      });
      if (current === version.current) {
        setPreview(response.data);
      }
    } catch (failure) {
      if (current !== version.current || axios.isCancel(failure)) return;
      const status = axios.isAxiosError(failure) ? failure.response?.status : undefined;
      const messages: Record<number, string> = {
        403: "Seu perfil não possui permissão para importar arquivos.",
        413: "O arquivo excede o limite permitido.",
        415: "O formato enviado não foi aceito.",
        422: "Não foi possível validar o arquivo. Confira os cabeçalhos e o formato dos dados.",
      };
      setError((status !== undefined ? messages[status] : undefined) ?? getApiErrorMessage(failure, "Não foi possível obter a prévia. Tente novamente."));
    } finally {
      if (current === version.current) setBusy(false);
    }
  }

  async function confirmImport() {
    if (!file || !preview || !available || !approved || confirmingRef.current || receipt || preview.invalid_rows > 0 || preview.valid_rows === 0) return;
    if (!key.current) {
      try { key.current = createImportIntentId(); }
      catch { setError('Não foi possível preparar a confirmação neste navegador.'); return; }
    }
    confirmingRef.current = true;
    setConfirming(true);
    setError("");
    const controller = new AbortController();
    request.current = controller;
    try {
      const response = await api.post<Receipt>("/imports/confirm", file, {
        params: confirmationParams(file.name, preview), timeout: 60000,
        headers: { "Content-Type": "application/octet-stream", "Idempotency-Key": key.current },
        signal: controller.signal,
      });
      setReceipt(response.data);
      setUncertain(false);
    } catch (failure) {
      const status = axios.isAxiosError(failure) ? failure.response?.status : undefined;
      // A lost response or server failure can occur after commit. Keep the
      // exact file and intent ID; only a receipt resolves the outcome.
      if (uncertain || status === undefined || status >= 500) {
        setUncertain(true);
        setError("O resultado ainda não foi confirmado. Use “Verificar resultado” antes de enviar outro arquivo e não recarregue esta tela.");
      } else {
        setUncertain(false);
        const code = axios.isAxiosError(failure) ? failure.response?.data?.detail?.code : undefined;
        const conflicts: Record<string, string> = {
          content_already_imported: "Este conteúdo já foi importado. Consulte os registros existentes.",
          existing_identity_conflict: "O arquivo inteiro foi recusado e nenhum registro foi importado: ao menos uma fatura já está cadastrada com o mesmo cliente, valor e vencimento. Envie um arquivo somente com faturas novas, sem alterar valores ou datas para contornar a verificação.",
          file_mismatch: "O arquivo mudou. Confira uma nova prévia antes de confirmar.",
          content_mismatch: "A prévia mudou. Valide novamente o arquivo.",
          idempotency_conflict: "A confirmação não corresponde à prévia. Valide novamente o arquivo.",
        };
        setError((typeof code === "string" ? conflicts[code] : undefined) ?? getApiErrorMessage(failure, "Não foi possível confirmar. Revise o arquivo e seu acesso."));
      }
    } finally {
      confirmingRef.current = false;
      setConfirming(false);
    }
  }

  return (
    <div className="page">
      <Header title="Importar CSV/Excel" subtitle="Confira os registros antes de importar" />
      <section className="page-content import-flow" aria-busy={busy || confirming}>
        <ol className="import-flow__steps" aria-label="Etapas da importação">
          <li aria-current={!preview ? "step" : undefined}>1. Selecionar arquivo</li>
          <li aria-current={preview && !approved && !receipt ? "step" : undefined}>2. Conferir prévia</li>
          <li aria-current={approved || receipt ? "step" : undefined}>3. Confirmar importação</li>
        </ol>
        <div className="import-flow__card">
          <FileUp size={28} aria-hidden="true" />
          <h2>Selecione seu arquivo</h2>
          <p>CSV ou XLSX, até 5 MiB. Colunas: cliente, valor, vencimento e status; email e whatsapp são opcionais.</p>
          <label htmlFor="import-file">Arquivo CSV ou Excel</label>
          <input id="import-file" type="file" accept=".csv,.xlsx" disabled={locked} onChange={(event) => selectFile(event.target.files?.[0] ?? null)} />
          <p className="import-flow__hint">A prévia não grava registros e não exibe nomes, contatos ou valores por linha.</p>
          <button type="button" disabled={!file || busy || locked} onClick={() => void loadPreview()}>{busy ? "Validando arquivo…" : "Conferir prévia"}</button>
        </div>
        {error && <p className="import-flow__error" role="alert">{error}</p>}
        <div role="status" aria-live="polite">{confirming ? "Aguarde a confirmação. Não feche esta tela." : busy ? "Aguarde a validação do arquivo." : preview && !receipt && !uncertain ? "Prévia disponível. Nenhum registro foi importado por esta confirmação." : ""}</div>
        {preview && <div className="import-flow__card">
          <h2>Confira o resultado da validação</h2>
          <dl className="import-flow__summary">
            <div><dt>Registros encontrados</dt><dd>{preview.total_rows}</dd></div>
            <div><dt>Válidos no arquivo</dt><dd>{preview.valid_rows}</dd></div>
            <div><dt>Com erros</dt><dd>{preview.invalid_rows}</dd></div>
            <div><dt>Total válido</dt><dd>{currency(preview.valid_total)}</dd></div>
          </dl>
          <p>Registros válidos ainda são verificados contra duplicidades durante a confirmação. Se houver conflito ou linha inválida, o lote inteiro é recusado.</p>
          <p className="import-flow__hint">Faturas com o mesmo cliente, valor e vencimento são tratadas como a mesma fatura, no arquivo ou já cadastradas. O nome do cliente é comparado sem diferenciar maiúsculas, minúsculas e espaços extras.</p>
          <div className="import-flow__table"><table><caption>Validação por linha{preview.rows_truncated ? " — primeiras 100 linhas" : ""}</caption><thead><tr><th scope="col">Linha</th><th scope="col">Resultado</th><th scope="col">Observações</th></tr></thead><tbody>
            {preview.rows.map((row) => <tr key={row.row_number}><td>{row.row_number}</td><td>{row.valid ? "Válida" : "Revisar"}</td><td>{row.issues.map((issue) => issue.message).join("; ") || "Sem erros de validação"}</td></tr>)}
          </tbody></table></div>
          {receipt ? <div role="status"><h3>{receipt.replayed ? "Importação já confirmada" : "Importação concluída"}</h3><p>{records(receipt.record_count)} · {currency(receipt.total)}</p></div> : <>
            <p id="import-confirm-help">{accessDenied ? "Seu acesso não permite confirmar importações. Entre novamente ou solicite a permissão ao administrador." : !available ? "A confirmação ainda não está disponível neste ambiente." : uncertain ? "Mantenha esta tela aberta: o arquivo, a autorização e a identificação desta confirmação são preservados. Use “Verificar resultado”; somente o recibo da importação confirma o resultado." : preview.invalid_rows > 0 ? "Corrija todas as linhas com erro e envie o arquivo novamente." : "Confira a prévia e autorize a importação dos registros válidos."}</p>
            {available && <label className="import-flow__approve"><input type="checkbox" checked={approved} disabled={locked || preview.invalid_rows > 0 || preview.valid_rows === 0} onChange={(event) => setApproved(event.target.checked)} />Conferi a prévia e autorizo importar {records(preview.valid_rows)}.</label>}
            <button type="button" disabled={!available || !approved || confirming || preview.invalid_rows > 0 || preview.valid_rows === 0} aria-describedby="import-confirm-help" onClick={() => void confirmImport()}>{confirming ? "Confirmando…" : uncertain ? "Verificar resultado" : "Confirmar importação"}</button>
          </>}
        </div>}
      </section>
    </div>
  );
}
