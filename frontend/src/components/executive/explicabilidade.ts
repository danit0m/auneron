// Monta o conteudo do cartao "Explicabilidade da analise" SO com o que a analise registrou.
// Nada e presumido: sem pesos, percentuais ou caracteristicas do cliente que nao venham dos dados.
// Informacao ausente e omitida (ou sinalizada como indisponivel), nunca preenchida por padrao.

export interface SinalAnalise {
  name: string;
  value: string | number | boolean | null;
  description: string;
}

export interface ContextoAnalise {
  cliente?: string | null;
  valor?: number | null;
  status?: string | null;
  vencimento?: string | null;
  dias_atraso?: number | null;
}

export interface DecisaoAnalise {
  decision_name: string;
  reason?: string | null;
  confidence_percentage?: number | null;
  signals?: SinalAnalise[] | null;
  context?: ContextoAnalise | null;
}

export interface FatorExplicado {
  chave: string;
  titulo: string;
  valor: string;
  descricao: string | null;
}

export interface Explicabilidade {
  fatores: FatorExplicado[];
  marcadores: string[];
  justificativa: string | null;
  confianca: string | null;
  semDados: boolean;
}

type Rotulo = (nome: string) => string;

const moeda = (v: number) =>
  new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(v);

const numeroValido = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const textoValido = (v: unknown): v is string => typeof v === "string" && v.trim() !== "";

function formatarValorSinal(nome: string, v: SinalAnalise["value"]): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "boolean") return v ? "Sim" : "Não";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return null;
    const n = nome.toLowerCase().replace(/[^a-z]/g, "");
    // Formato pelo nome do dado: valores em reais e contagem de dias; o numero em si nao muda.
    if (n === "valor" || n === "valoremaberto") return moeda(v);
    if (n === "diasatraso") return `${v} ${v === 1 ? "dia" : "dias"}`;
    return new Intl.NumberFormat("pt-BR").format(v);
  }
  return textoValido(v) ? v.trim() : null;
}

function formatarSituacao(status: string): string {
  const s = status.trim().toLowerCase();
  const mapa: Record<string, string> = { atrasado: "Atrasado", aberto: "Em aberto", pago: "Pago" };
  return mapa[s] ?? status.trim();
}

export function montarExplicabilidade(d: DecisaoAnalise, rotuloSinal: Rotulo): Explicabilidade {
  const fatores: FatorExplicado[] = [];
  const ctx = d.context ?? {};

  // 1. Sinais registrados pela propria analise (a fonte principal da explicacao).
  for (const s of d.signals ?? []) {
    if (!s || !textoValido(s.name)) continue;
    const valor = formatarValorSinal(s.name, s.value);
    if (valor === null) continue;
    fatores.push({
      chave: `sinal:${s.name}`,
      titulo: rotuloSinal(s.name),
      valor,
      descricao: textoValido(s.description) ? s.description.trim() : null,
    });
  }

  // 2. Dados do titulo analisado, so os que existem e ainda nao vieram como sinal.
  const norm = (t: string) => t.toLowerCase().replace(/[^a-z]/g, "");
  // So evita repetir quando a analise ja trouxe exatamente o mesmo dado como sinal.
  const jaTem = (nome: string) => fatores.some((f) => norm(f.chave.replace(/^sinal:/, "")) === nome);
  if (numeroValido(ctx.valor) && !jaTem("valor")) {
    fatores.push({ chave: "contexto:valor", titulo: "Valor em aberto", valor: moeda(ctx.valor), descricao: null });
  }
  if (textoValido(ctx.status) && !jaTem("status")) {
    fatores.push({ chave: "contexto:status", titulo: "Situação do título", valor: formatarSituacao(ctx.status), descricao: null });
  }
  if (numeroValido(ctx.dias_atraso) && ctx.dias_atraso > 0 && !jaTem("diasatraso")) {
    fatores.push({
      chave: "contexto:dias_atraso",
      titulo: "Dias em atraso",
      valor: `${ctx.dias_atraso} ${ctx.dias_atraso === 1 ? "dia" : "dias"}`,
      descricao: null,
    });
  }

  // 3. Marcadores: so fatos verificaveis nos dados. Nenhuma caracteristica do cliente e presumida.
  const marcadores: string[] = [];
  const atrasado = (textoValido(ctx.status) && ctx.status.trim().toLowerCase() === "atrasado")
    || (numeroValido(ctx.dias_atraso) && ctx.dias_atraso > 0);
  if (atrasado) marcadores.push("Pagamento atrasado");
  if (numeroValido(ctx.dias_atraso) && ctx.dias_atraso > 0) {
    marcadores.push(`${ctx.dias_atraso} ${ctx.dias_atraso === 1 ? "dia" : "dias"} em atraso`);
  }

  const confianca = numeroValido(d.confidence_percentage)
    ? `${new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 }).format(d.confidence_percentage)}%`
    : null;

  return {
    fatores,
    marcadores,
    justificativa: textoValido(d.reason) ? d.reason.trim() : null,
    confianca,
    semDados: fatores.length === 0,
  };
}
