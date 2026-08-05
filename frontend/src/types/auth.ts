export type UserRole =
  | "viewer"
  | "analyst"
  | "manager"
  | "executive"
  | "administrator"
  | "developer";

export type Permission =
  | "dashboard.view"
  | "clients.view"
  | "clients.manage"
  | "imports.execute"
  | "executive.view"
  | "brain.view"
  | "administration.ai-operations"
  | "developer.ui-showcase";

export interface AuthUser {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  active: boolean;
}

export interface AuthSession {
  user: AuthUser;
  authenticatedAt: string;
  expiresAt: string | null;
}

export interface AuthContextValue {
  user: AuthUser | null;
  session: AuthSession | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  isDevelopmentSession: boolean;
  permissions: readonly Permission[];
  hasPermission: (
    permission: Permission,
  ) => boolean;
  hasAnyPermission: (
    permissions: readonly Permission[],
  ) => boolean;
  hasAllPermissions: (
    permissions: readonly Permission[],
  ) => boolean;
  signOut: () => void;
  setDevelopmentRole: (
    role: UserRole,
  ) => void;
}
