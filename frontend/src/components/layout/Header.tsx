import { Bell, Menu, Search } from "lucide-react";

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
          aria-label="Abrir menu"
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

        <button
          type="button"
          className="header-icon-button"
          aria-label="Notificações"
        >
          <Bell size={20} />
          <span className="notification-dot" />
        </button>

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
