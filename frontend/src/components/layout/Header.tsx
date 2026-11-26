import { Menu, Search } from "lucide-react";
import { useEffect, useState } from "react";

import { AvisosBell } from "./AvisosBell";

import {
  roleLabels,
} from "../../auth";
import {
  useAuth,
} from "../../hooks/useAuth";

interface HeaderProps {
  title: string;
  subtitle?: string;
}

function getUserInitials(
  name: string,
): string {
  const parts = name
    .trim()
    .split(/\s+/)
    .filter(Boolean);

  if (parts.length === 0) {
    return "AU";
  }

  if (parts.length === 1) {
    return parts[0]
      .slice(0, 2)
      .toUpperCase();
  }

  return (
    parts[0][0] +
    parts[parts.length - 1][0]
  ).toUpperCase();
}

export function Header({
  title,
  subtitle,
}: HeaderProps) {
  const {
    user,
  } = useAuth();

  // Menu lateral em telas estreitas (<= 950 px): o botao abre a mesma navegacao do menu, sobreposta.
  const [menuAberto, setMenuAberto] = useState(false);

  useEffect(() => {
    const fechar = () => setMenuAberto(false);
    const tecla = (e: KeyboardEvent) => { if (e.key === "Escape") fechar(); };
    window.addEventListener("auneron:fechar-menu", fechar);
    window.addEventListener("keydown", tecla);
    return () => {
      window.removeEventListener("auneron:fechar-menu", fechar);
      window.removeEventListener("keydown", tecla);
    };
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("menu-lateral-aberto", menuAberto);
    return () => document.documentElement.classList.remove("menu-lateral-aberto");
  }, [menuAberto]);

  const userName =
    user?.name ?? "Auneron AI";

  const userRole =
    user
      ? roleLabels[user.role]
      : "Sem sessão";

  const initials =
    getUserInitials(userName);

  return (
    <header className="header">
      <div className="header-title-area">
        <button
          type="button"
          className="mobile-menu-button"
          aria-label={menuAberto ? "Fechar menu" : "Abrir menu"}
          aria-expanded={menuAberto}
          onClick={() => setMenuAberto((aberto) => !aberto)}
        >
          <Menu size={22} />
        </button>

        <div>
          <h1>{title}</h1>

          {subtitle && (
            <p>{subtitle}</p>
          )}
        </div>
      </div>

      <div className="header-actions">
        <div className="header-search">
          <Search size={18} />

          <input
            type="search"
            placeholder="Pesquisar..."
            aria-label="Pesquisar"
          />
        </div>

        <AvisosBell />

        <div className="user-profile">
          <div className="user-avatar">
            {initials}
          </div>

          <div className="user-profile-text">
            <strong>
              {userName}
            </strong>

            <span>
              {userRole}
            </span>
          </div>
        </div>
      </div>
    </header>
  );
}
