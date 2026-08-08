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
  id: number;
  name: string;
  email: string;
  role: UserRole;
  active: boolean;
}

export interface AuthSession {
  user: AuthUser;
  authenticatedAt: string;
  expiresAt: string;
  elevatedUntil: string | null;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface AuthContextValue {
  user: AuthUser | null;
  session: AuthSession | null;
  isAuthenticated: boolean;
  isLoading: boolean;
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
  signIn: (
    credentials: LoginCredentials,
  ) => Promise<AuthSession>;
  signOut: () => Promise<void>;
  refreshSession: () => Promise<AuthSession | null>;
}
