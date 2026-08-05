import {
  type ReactNode,
  useCallback,
  useMemo,
  useState,
} from "react";

import {
  canAccess,
  canAccessAll,
  canAccessAny,
  getPermissionsForRole,
  isUserRole,
} from "../auth";
import {
  AuthContext,
} from "../contexts/AuthContext";
import type {
  AuthSession,
  AuthUser,
  UserRole,
} from "../types/auth";

interface AuthProviderProps {
  children: ReactNode;
}

const DEVELOPMENT_ROLE_KEY =
  "auneron.auth.development-role";

function createDevelopmentUser(
  role: UserRole,
): AuthUser {
  return {
    id: "development-user",
    name: "Daniel Tomaz",
    email: "daniel@auneron.local",
    role,
    active: true,
  };
}

function createDevelopmentSession(
  role: UserRole,
): AuthSession {
  return {
    user: createDevelopmentUser(role),
    authenticatedAt:
      new Date().toISOString(),
    expiresAt: null,
  };
}

function getInitialDevelopmentRole(): UserRole {
  if (!import.meta.env.DEV) {
    return "viewer";
  }

  try {
    const storedRole =
      window.localStorage.getItem(
        DEVELOPMENT_ROLE_KEY,
      );

    if (isUserRole(storedRole)) {
      return storedRole;
    }
  } catch {
    // Mantém o papel padrão em memória.
  }

  return "developer";
}

export function AuthProvider({
  children,
}: AuthProviderProps) {
  const isDevelopmentSession =
    import.meta.env.DEV;

  const [
    session,
    setSession,
  ] = useState<AuthSession | null>(() => {
    if (!isDevelopmentSession) {
      return null;
    }

    return createDevelopmentSession(
      getInitialDevelopmentRole(),
    );
  });

  const user =
    session?.user ?? null;

  const permissions = useMemo(
    () =>
      user
        ? getPermissionsForRole(
            user.role,
          )
        : [],
    [user],
  );

  const hasPermission = useCallback(
    (
      permission:
        Parameters<typeof canAccess>[1],
    ) => {
      return canAccess(
        user,
        permission,
      );
    },
    [user],
  );

  const hasAnyPermission =
    useCallback(
      (
        requiredPermissions:
          Parameters<
            typeof canAccessAny
          >[1],
      ) => {
        return canAccessAny(
          user,
          requiredPermissions,
        );
      },
      [user],
    );

  const hasAllPermissions =
    useCallback(
      (
        requiredPermissions:
          Parameters<
            typeof canAccessAll
          >[1],
      ) => {
        return canAccessAll(
          user,
          requiredPermissions,
        );
      },
      [user],
    );

  const signOut = useCallback(() => {
    setSession(null);
  }, []);

  const setDevelopmentRole =
    useCallback(
      (role: UserRole) => {
        if (!isDevelopmentSession) {
          return;
        }

        try {
          window.localStorage.setItem(
            DEVELOPMENT_ROLE_KEY,
            role,
          );
        } catch {
          // A sessão continua válida
          // durante a execução atual.
        }

        setSession(
          createDevelopmentSession(role),
        );
      },
      [isDevelopmentSession],
    );

  const value = useMemo(
    () => ({
      user,
      session,
      isAuthenticated:
        Boolean(user),
      isLoading: false,
      isDevelopmentSession,
      permissions,
      hasPermission,
      hasAnyPermission,
      hasAllPermissions,
      signOut,
      setDevelopmentRole,
    }),
    [
      user,
      session,
      isDevelopmentSession,
      permissions,
      hasPermission,
      hasAnyPermission,
      hasAllPermissions,
      signOut,
      setDevelopmentRole,
    ],
  );

  return (
    <AuthContext.Provider
      value={value}
    >
      {children}
    </AuthContext.Provider>
  );
}
