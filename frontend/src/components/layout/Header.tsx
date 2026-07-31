import { Bell, Menu, Search } from "lucide-react";

interface HeaderProps {
  title: string;
  subtitle?: string;
}

export function Header({ title, subtitle }: HeaderProps) {
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

          {subtitle && <p>{subtitle}</p>}
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
          <div className="user-avatar">DT</div>

          <div className="user-profile-text">
            <strong>Daniel Tomaz</strong>
            <span>Administrador</span>
          </div>
        </div>
      </div>
    </header>
  );
}