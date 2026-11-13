import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useMemo } from "react";

import { AuneronBot, Card } from "../ui";
import type { BotMood } from "../ui";
import { formatarMoeda } from "../../lib/cockpit";
import {
  montarSaudeCarteira,
  type FaixaCarteira,
  type FiltroCarteira,
} from "../../lib/carteira";
import type { Account } from "../../types/account";
import "./SaudeCarteira.css";

interface Props {
  contas: Account[];
  filtro: FiltroCarteira;
  onFiltrar: (filtro: FiltroCarteira) => void;
}

const CARTOES: ReadonlyArray<{
  faixa: FaixaCarteira;
  titulo: string;
  mood: BotMood | null;
  rotuloValor: (valor: string) => string;
}> = [
  { faixa: "em-dia", titulo: "Em dia", mood: "feliz", rotuloValor: (v) => `${v} em aberto` },
  { faixa: "ate-30", titulo: "Atraso de até 30 dias", mood: "preocupado", rotuloValor: (v) => v },
  { faixa: "mais-30", titulo: "Atraso acima de 30 dias", mood: "critico", rotuloValor: (v) => v },
  { faixa: "pagos", titulo: "Pagos", mood: null, rotuloValor: (v) => `${v} pagos` },
];

function rotuloClientes(n: number): string {
  return n === 1 ? "cliente" : "clientes";
}

export default function SaudeCarteira({ contas, filtro, onFiltrar }: Props) {
  const dados = useMemo(() => montarSaudeCarteira(contas, new Date()), [contas]);

  if (!dados) return null;

  const { faixas, concentracao, semContato } = dados;

  function alternar(novo: Exclude<FiltroCarteira, null>) {
    onFiltrar(filtro === novo ? null : novo);
  }

  return (
    <section className="saude-carteira" aria-label="Saúde da carteira">
      <div className="saude-faixas">
        {CARTOES.map((cartao) => {
          const faixa = faixas[cartao.faixa];
          return (
            <button
              type="button"
              key={cartao.faixa}
              className={`saude-faixa saude-faixa-${cartao.faixa}`}
              aria-pressed={filtro === cartao.faixa}
              onClick={() => alternar(cartao.faixa)}
            >
              {cartao.mood ? (
                <AuneronBot mood={cartao.mood} size="md" />
              ) : (
                <CheckCircle2 size={40} aria-hidden="true" className="saude-faixa-icone" />
              )}
              <span className="saude-faixa-texto">
                <span className="saude-faixa-titulo">{cartao.titulo}</span>
                <span className="saude-faixa-numero">
                  <strong>{faixa.clientes}</strong>
                  <span>{rotuloClientes(faixa.clientes)}</span>
                </span>
                <small>{cartao.rotuloValor(formatarMoeda(faixa.valor))}</small>
              </span>
            </button>
          );
        })}
      </div>

      {semContato && (
        <div className="saude-aviso" role="note">
          <AlertTriangle size={18} aria-hidden="true" />
          <span>
            {semContato.clientes === 1
              ? "1 cliente com valor em aberto está sem contato"
              : `${semContato.clientes} clientes com valor em aberto estão sem contato`}
          </span>
          <button
            type="button"
            className="saude-aviso-ver"
            aria-pressed={filtro === "sem-contato"}
            onClick={() => alternar("sem-contato")}
          >
            Ver
          </button>
        </div>
      )}

      {concentracao && (
        <Card padding="lg" className="saude-concentracao">
          <h3>Quanto você depende de poucos clientes</h3>
          <p className="saude-sub">Participação no valor em aberto</p>

          <div className="saude-topo">
            <strong>{concentracao.percentualTopo}%</strong>
            <span>
              vem de {concentracao.quantosNoTopo} {rotuloClientes(concentracao.quantosNoTopo)}
            </span>
          </div>

          <ul className="saude-barras">
            {concentracao.barras.map((barra, i) => (
              <li key={`${barra.nome}-${i}`}>
                <span className="saude-barra-nome">{barra.nome}</span>
                <span className="saude-barra-trilho">
                  <span
                    className="saude-barra-preench"
                    style={{ width: `${Math.min(100, barra.percentual)}%` }}
                  />
                </span>
                <span className="saude-barra-pct">{barra.percentual}%</span>
              </li>
            ))}
            {concentracao.outros && (
              <li className="saude-barra-outros">
                <span className="saude-barra-nome">
                  Outros {concentracao.outros.clientes}{" "}
                  {rotuloClientes(concentracao.outros.clientes)}
                </span>
                <span className="saude-barra-trilho">
                  <span
                    className="saude-barra-preench"
                    style={{ width: `${Math.min(100, concentracao.outros.percentual)}%` }}
                  />
                </span>
                <span className="saude-barra-pct">{concentracao.outros.percentual}%</span>
              </li>
            )}
          </ul>
        </Card>
      )}
    </section>
  );
}
