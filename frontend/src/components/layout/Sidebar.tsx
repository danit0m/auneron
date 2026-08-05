import {
  BarChart3,
  BrainCircuit,
  FileUp,
  LayoutDashboard,
  LockKeyhole,
  Palette,
  ServerCog,
  ShieldCheck,
  Users,
  WalletCards,
} from "lucide-react";
import type {
  LucideIcon,
} from "lucide-react";
import {
  NavLink,
} from "react-router-dom";

import {
  privilegedPermissions,
} from "../../auth";
import {
  useAuth,
} from "../../hooks/useAuth";
import type {
  Permission,
} from "../../types/auth";

interface MenuItem {
  label: string;
  path: string;
  icon: LucideIcon;
  permission: Permission;
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
    permission: "dashboard.view",
    end: true,
  },
  {
    label: "Clientes",
    path: "/clientes",
    icon: Users,
    permission: "clients.view",
  },
  {
    label: "Importar CSV",
    path: "/upload",
    icon: FileUp,
    permission: "imports.execute",
  },
];

const intelligenceItems: MenuItem[] = [
  {
    label: "Executive Center",
    path: "/executive-center",
    icon: ShieldCheck,
    permission: "executive.view",
  },
  {
    label: "Brain",
    path: "/brain",
    icon: BrainCircuit,
    permission: "brain.view",
  },
];

const administrationItems: MenuItem[] = [
  {
    label: "AI Operations",
    path: "/agent-operations",
    icon: ServerCog,
    permission:
      "administration.ai-operations",
  },
];

const developerToolsItems: MenuItem[] = [
  {
    label: "UI Showcase",
    path: "/admin/ui-showcase",
    icon: Palette,
    permission:
      "developer.ui-showcase",
  },
];

function isPrivilegedPermission(
  permission: Permission,
): boolean {
  return privilegedPermissions.includes(
    permission as
      (typeof privilegedPermissions)[number],
  );
}

function MenuSection({
  title,
  items,
}: MenuSectionProps) {
  const {
    hasPermission,
  } = useAuth();

  const visibleItems = items.filter(
    (item) =>
      hasPermission(item.permission),
  );

  if (visibleItems.length === 0) {
    return null;
  }

  return (
    <div className="sidebar-menu-section">
      <p className="sidebar-section-title">
        {title}
      </p>

      {visibleItems.map((item) => {
        const Icon = item.icon;
        const privileged =
          isPrivilegedPermission(
            item.permission,
          );

        return (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.end}
            title={
              privileged
                ? `${item.label} — acesso privilegiado`
                : item.label
            }
            className={({ isActive }) =>
              isActive
                ? "sidebar-link sidebar-link-active"
                : "sidebar-link"
            }
          >
            <Icon size={20} />

            <span>{item.label}</span>

            {privileged && (
              <LockKeyhole
                size={14}
                className="sidebar-link-security-icon"
                aria-label="Acesso privilegiado"
              />
            )}
          </NavLink>
        );
      })}
    </div>
  );
}

export function Sidebar() {
  const {
    user,
    isDevelopmentSession,
  } = useAuth();

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

        <MenuSection
          title="DEVELOPER TOOLS"
          items={developerToolsItems}
        />
      </nav>

      <div className="sidebar-footer">
        <BarChart3 size={20} />

        <div>
          <strong>
            {user?.name ?? "Auneron AI"}
          </strong>

          <span>
            {isDevelopmentSession
              ? `DEV · ${user?.role ?? "sem sessão"}`
              : "Versão 3.0 Alpha"}
          </span>
        </div>
      </div>
    </aside>
  );
}
