import {
  BarChart3,
  BrainCircuit,
  FileUp,
  LayoutDashboard,
  ServerCog,
  ShieldCheck,
  Users,
  WalletCards,
} from "lucide-react";
import {
  type LucideIcon,
} from "lucide-react";
import { NavLink } from "react-router-dom";

interface MenuItem {
  label: string;
  path: string;
  icon: LucideIcon;
  end?: boolean;
}

interface MenuSectionProps {
  title: string;
  items: MenuItem[];
}

const principalItems: MenuItem[] = [
  {
    label: "Dashboard",
    path: "/",
    icon: LayoutDashboard,
    end: true,
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

const intelligenceItems: MenuItem[] = [
  {
    label: "Executive Center",
    path: "/executive-center",
    icon: ShieldCheck,
  },
  {
    label: "Brain",
    path: "/brain",
    icon: BrainCircuit,
  },
];

const administrationItems: MenuItem[] = [
  {
    label: "AI Operations",
    path: "/agent-operations",
    icon: ServerCog,
  },
];

function MenuSection({
  title,
  items,
}: MenuSectionProps) {
  return (
    <div className="sidebar-menu-section">
      <p className="sidebar-section-title">
        {title}
      </p>

      {items.map((item) => {
        const Icon = item.icon;

        return (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.end}
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
    </div>
  );
}

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
        <MenuSection
          title="MENU PRINCIPAL"
          items={principalItems}
        />

        <MenuSection
          title="INTELIGÊNCIA"
          items={intelligenceItems}
        />

        <MenuSection
          title="ADMINISTRAÇÃO"
          items={administrationItems}
        />
      </nav>

      <div className="sidebar-footer">
        <BarChart3 size={20} />

        <div>
          <strong>Auneron AI</strong>

          <span>
            Versão 3.0 Alpha
          </span>
        </div>
      </div>
    </aside>
  );
}