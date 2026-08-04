import {
  BarChart3,
  BrainCircuit,
  FileUp,
  LayoutDashboard,
  ServerCog,
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
  {
    label: "Brain",
    path: "/brain",
    icon: BrainCircuit,
  },
  {
    label: "Agent Operations",
    path: "/agent-operations",
    icon: ServerCog,
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
          <strong>Auneron AI</strong>
          <span>
            Business Intelligence
          </span>
        </div>
      </div>

      <nav className="sidebar-nav">
        <p className="sidebar-section-title">
          MENU PRINCIPAL
        </p>

        {menuItems.map((item) => {
          const Icon = item.icon;

          return (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/"}
              className={({
                isActive,
              }) =>
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
          <strong>Auneron AI</strong>
          <span>Versão 3.0 Alpha</span>
        </div>
      </div>
    </aside>
  );
}