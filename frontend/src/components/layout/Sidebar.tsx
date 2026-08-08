import {
  BarChart3,
  BrainCircuit,
  FileUp,
  LayoutDashboard,
  LockKeyhole,
  LogOut,
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
  useState,
} from "react";

import {
  privilegedPermissions,
  roleLabels,
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
    signOut,
  } = useAuth();

  const [
    signingOut,
    setSigningOut,
  ] = useState(false);

  async function handleSignOut() {
    if (signingOut) {
      return;
    }

    setSigningOut(true);

    try {
      await signOut();
    } finally {
      setSigningOut(false);
    }
  }

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

        <div
          style={{
            minWidth: 0,
            flex: 1,
          }}
        >
          <strong>
            {user?.name ?? "Auneron AI"}
          </strong>

          <span>
            {user
              ? roleLabels[user.role]
              : "Sem sessão"}
          </span>
        </div>

        <button
          type="button"
          title="Sair"
          aria-label="Sair"
          disabled={signingOut}
          onClick={() =>
            void handleSignOut()
          }
          style={{
            width: 34,
            height: 34,
            display: "grid",
            placeItems: "center",
            flex: "0 0 auto",
            border: "1px solid var(--border-subtle, #dbe2ea)",
            borderRadius: 9,
            cursor: signingOut
              ? "wait"
              : "pointer",
            color: "var(--text-secondary, #475569)",
            background:
              "var(--surface-primary, #ffffff)",
            opacity: signingOut ? 0.6 : 1,
          }}
        >
          <LogOut size={17} />
        </button>
      </div>
    </aside>
  );
}
