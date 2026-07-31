import {
  CalendarDays,
  Mail,
  MessageCircle,
  Pencil,
  Trash2,
  Users,
} from "lucide-react";

import type { Account } from "../../types/account";

interface ClienteTableProps {
  clientes: Account[];
  onEdit: (cliente: Account) => void;
  onDelete: (cliente: Account) => void;
}

function formatarMoeda(valor: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  }).format(valor);
}

function formatarData(data: string): string {
  const [ano, mes, dia] = data.split("-");

  if (!ano || !mes || !dia) {
    return data;
  }

  return `${dia}/${mes}/${ano}`;
}

function obterClasseStatus(status: string): string {
  const statusNormalizado = status.toLowerCase();

  if (statusNormalizado === "pago") {
    return "status-badge status-paid";
  }

  if (statusNormalizado === "atrasado") {
    return "status-badge status-late";
  }

  return "status-badge status-open";
}

function obterIniciais(nome: string): string {
  return nome
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((parte) => parte[0])
    .join("")
    .toUpperCase();
}

export default function ClienteTable({
  clientes,
  onEdit,
  onDelete,
}: ClienteTableProps) {
  if (clientes.length === 0) {
    return (
      <div className="clientes-empty-state">
        <div className="clientes-empty-icon">
          <Users size={30} />
        </div>

        <h3>Nenhum cliente encontrado</h3>

        <p>
          Ajuste a pesquisa ou cadastre um novo cliente para começar.
        </p>
      </div>
    );
  }

  return (
    <div className="clientes-table-wrapper">
      <table className="clientes-table">
        <thead>
          <tr>
            <th>Cliente</th>
            <th>Contato</th>
            <th>Status</th>
            <th>Valor</th>
            <th>Vencimento</th>
            <th className="clientes-actions-heading">Ações</th>
          </tr>
        </thead>

        <tbody>
          {clientes.map((cliente) => (
            <tr key={cliente.id}>
              <td>
                <div className="cliente-identity">
                  <div className="cliente-avatar">
                    {obterIniciais(cliente.cliente)}
                  </div>

                  <div className="cliente-name">
                    <strong>{cliente.cliente}</strong>
                    <span>Cliente #{cliente.id}</span>
                  </div>
                </div>
              </td>

              <td>
                <div className="cliente-contact">
                  {cliente.email ? (
                    <span>
                      <Mail size={14} />
                      {cliente.email}
                    </span>
                  ) : (
                    <span className="cliente-contact-empty">
                      E-mail não informado
                    </span>
                  )}

                  {cliente.whatsapp ? (
                    <span>
                      <MessageCircle size={14} />
                      {cliente.whatsapp}
                    </span>
                  ) : (
                    <span className="cliente-contact-empty">
                      WhatsApp não informado
                    </span>
                  )}
                </div>
              </td>

              <td>
                <span className={obterClasseStatus(cliente.status)}>
                  {cliente.status}
                </span>
              </td>

              <td>
                <strong className="cliente-value">
                  {formatarMoeda(cliente.valor)}
                </strong>
              </td>

              <td>
                <span className="cliente-date">
                  <CalendarDays size={15} />
                  {formatarData(cliente.vencimento)}
                </span>
              </td>

              <td>
                <div className="cliente-row-actions">
                  <button
                    type="button"
                    className="cliente-action-button cliente-edit-button"
                    aria-label={`Editar ${cliente.cliente}`}
                    title="Editar cliente"
                    onClick={() => onEdit(cliente)}
                  >
                    <Pencil size={16} />
                  </button>

                  <button
                    type="button"
                    className="cliente-action-button cliente-delete-button"
                    aria-label={`Excluir ${cliente.cliente}`}
                    title="Excluir cliente"
                    onClick={() => onDelete(cliente)}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}