import {
  BarChart3,
  FileUp,
  LayoutDashboard,
  Users,
  WalletCards,
} from "lucide-react";
import { NavLink } from "react-router-dom";

const menuItems = [
  {
    label: "Dashboard",
    path: "/",
    icon: LayoutDashboard,
  },
  {
    label: "Clientes",
    path: "/clientes",
    icon: Users,
  },
  {
    label: "Importar CSV",
    path: "/upload",
    icon: FileUp,
  },
];

export function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-logo">
          <WalletCards size={25} />
        </div>

        <div>
          <strong>Auneron</strong>
          <span>Finance</span>
        </div>
      </div>

      <nav className="sidebar-nav">
        <p className="sidebar-section-title">MENU PRINCIPAL</p>

        {menuItems.map((item) => {
          const Icon = item.icon;

          return (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/"}
              className={({ isActive }) =>
                isActive
                  ? "sidebar-link sidebar-link-active"
                  : "sidebar-link"
              }
            >
              <Icon size={20} />
              <span>{item.label}</span>
            </NavLink>
          );
        })}
      </nav>

      <div className="sidebar-footer">
        <BarChart3 size={20} />

        <div>
          <strong>Auneron Finance</strong>
          <span>Versão 1.0</span>
        </div>
      </div>
    </aside>
  );
}