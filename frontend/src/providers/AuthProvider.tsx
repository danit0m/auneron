import axios from "axios";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import api from "../api/api";
import {
  canAccess,
  canAccessAll,
  canAccessAny,
  getPermissionsForRole,
} from "../auth";
import {
  AuthContext,
} from "../contexts/AuthContext";
import type {
  AuthSession,
  LoginCredentials,
} from "../types/auth";

interface AuthProviderProps {
  children: ReactNode;
}

interface AuthSessionApiResponse {
  user: AuthSession["user"];
  authenticated_at: string;
  expires_at: string;
  elevated_until: string | null;
}

function mapSession(
  payload: AuthSessionApiResponse,
): AuthSession {
  return {
    user: payload.user,
    authenticatedAt:
      payload.authenticated_at,
    expiresAt: payload.expires_at,
    elevatedUntil:
      payload.elevated_until,
  };
}

export function AuthProvider({
  children,
}: AuthProviderProps) {
  const [
    session,
    setSession,
  ] = useState<AuthSession | null>(
    null,
  );

  const [
    isLoading,
    setIsLoading,
  ] = useState(true);

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

  const refreshSession =
    useCallback(
      async (): Promise<AuthSession | null> => {
        try {
          const response =
            await api.get<AuthSessionApiResponse>(
              "/auth/me",
            );

          const nextSession =
            mapSession(response.data);

          setSession(nextSession);

          return nextSession;
        } catch (error) {
          if (
            axios.isAxiosError(error) &&
            error.response?.status === 401
          ) {
            setSession(null);

            return null;
          }

          throw error;
        }
      },
      [],
    );

  useEffect(() => {
    let active = true;

    async function restoreSession() {
      try {
        const response =
          await api.get<AuthSessionApiResponse>(
            "/auth/me",
          );

        if (!active) {
          return;
        }

        setSession(
          mapSession(response.data),
        );
      } catch (error) {
        if (!active) {
          return;
        }

        if (
          axios.isAxiosError(error) &&
          error.response?.status === 401
        ) {
          setSession(null);
        } else {
          console.error(
            "Não foi possível restaurar a sessão do usuário:",
            error,
          );

          setSession(null);
        }
      } finally {
        if (active) {
          setIsLoading(false);
        }
      }
    }

    void restoreSession();

    return () => {
      active = false;
    };
  }, []);

  const signIn = useCallback(
    async (
      credentials: LoginCredentials,
    ): Promise<AuthSession> => {
      const response =
        await api.post<AuthSessionApiResponse>(
          "/auth/login",
          credentials,
        );

      const nextSession =
        mapSession(response.data);

      setSession(nextSession);

      return nextSession;
    },
    [],
  );

  const signOut = useCallback(
    async () => {
      try {
        await api.post(
          "/auth/logout",
        );
      } catch (error) {
        if (
          !axios.isAxiosError(error) ||
          error.response?.status !== 401
        ) {
          console.error(
            "Não foi possível concluir o logout no backend:",
            error,
          );
        }
      } finally {
        setSession(null);
      }
    },
    [],
  );

  const value = useMemo(
    () => ({
      user,
      session,
      isAuthenticated:
        Boolean(user),
      isLoading,
      permissions,
      hasPermission,
      hasAnyPermission,
      hasAllPermissions,
      signIn,
      signOut,
      refreshSession,
    }),
    [
      user,
      session,
      isLoading,
      permissions,
      hasPermission,
      hasAnyPermission,
      hasAllPermissions,
      signIn,
      signOut,
      refreshSession,
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
