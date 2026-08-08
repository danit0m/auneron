import axios from "axios";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import api, {
  getApiErrorMessage,
} from "../api/api";
import {
  ElevationContext,
} from "../contexts/ElevationContext";
import {
  useAuth,
} from "../hooks/useAuth";
import {
  getRemainingSeconds,
  isElevationActive,
} from "../security/elevation";
import type {
  ElevationAttemptResult,
  ElevationStatus,
} from "../types/elevation";

interface ElevationProviderProps {
  children: ReactNode;
}

interface ElevationApiResponse {
  elevated_until: string;
}

interface ElevationOverride {
  sessionIdentity: string;
  elevatedUntil: string | null;
}

export function ElevationProvider({
  children,
}: ElevationProviderProps) {
  const {
    session,
    refreshSession,
  } = useAuth();

  const [
    operationStatus,
    setOperationStatus,
  ] = useState<
    "idle" | "validating" | "unavailable"
  >("idle");

  const [
    elevationOverride,
    setElevationOverride,
  ] = useState<ElevationOverride | null>(
    null,
  );

  const [, setClockTick] =
    useState(0);

  const sessionIdentity = session
    ? [
        session.user.id,
        session.authenticatedAt,
      ].join(":")
    : null;

  const serverExpiration =
    session?.elevatedUntil ?? null;

  const overrideApplies =
    sessionIdentity !== null &&
    elevationOverride?.sessionIdentity ===
      sessionIdentity;

  const elevatedUntil =
    overrideApplies
      ? elevationOverride.elevatedUntil
      : serverExpiration;

  const remainingSeconds =
    getRemainingSeconds(
      elevatedUntil,
    );

  const isElevated =
    remainingSeconds > 0;

  const status: ElevationStatus =
    operationStatus === "validating"
      ? "validating"
      : operationStatus ===
          "unavailable"
        ? "unavailable"
        : isElevated
          ? "elevated"
          : elevatedUntil
            ? "expired"
            : "idle";

  useEffect(() => {
    if (
      !isElevationActive(
        elevatedUntil,
      )
    ) {
      return;
    }

    const intervalId =
      window.setInterval(() => {
        setClockTick(
          (current) => current + 1,
        );
      }, 1000);

    return () => {
      window.clearInterval(
        intervalId,
      );
    };
  }, [elevatedUntil]);

  const requestElevation =
    useCallback(
      async (
        password: string,
      ): Promise<ElevationAttemptResult> => {
        setOperationStatus(
          "validating",
        );

        try {
          const response =
            await api.post<ElevationApiResponse>(
              "/auth/elevate",
              {
                password,
              },
            );

          const expiration =
            response.data.elevated_until;

          if (
            !isElevationActive(
              expiration,
            )
          ) {
            setOperationStatus(
              "unavailable",
            );

            return {
              success: false,
              message:
                "O backend não retornou uma elevação válida.",
            };
          }

          if (sessionIdentity) {
            setElevationOverride({
              sessionIdentity,
              elevatedUntil:
                expiration,
            });
          }

          setOperationStatus("idle");

          try {
            await refreshSession();

            setElevationOverride(
              null,
            );
          } catch (refreshError) {
            console.error(
              "A elevação foi aceita, mas a sessão não pôde ser sincronizada imediatamente:",
              refreshError,
            );
          }

          return {
            success: true,
            message:
              "Acesso elevado validado pelo servidor.",
          };
        } catch (error) {
          const invalidCredential =
            axios.isAxiosError(error) &&
            error.response?.status ===
              401 &&
            error.response?.data &&
            typeof error.response.data ===
              "object" &&
            "detail" in
              error.response.data &&
            error.response.data.detail ===
              "Credencial elevada inválida.";

          setOperationStatus(
            invalidCredential
              ? "idle"
              : "unavailable",
          );

          return {
            success: false,
            message:
              getApiErrorMessage(
                error,
                "Não foi possível validar o acesso elevado.",
              ),
          };
        }
      },
      [
        refreshSession,
        sessionIdentity,
      ],
    );

  const revokeElevation =
    useCallback(
      async () => {
        try {
          await api.post(
            "/auth/elevation/revoke",
          );
        } finally {
          if (sessionIdentity) {
            setElevationOverride({
              sessionIdentity,
              elevatedUntil: null,
            });
          }

          setOperationStatus("idle");
        }

        try {
          await refreshSession();

          setElevationOverride(
            null,
          );
        } catch (refreshError) {
          console.error(
            "A elevação foi revogada, mas a sessão não pôde ser sincronizada imediatamente:",
            refreshError,
          );
        }
      },
      [
        refreshSession,
        sessionIdentity,
      ],
    );

  const value = useMemo(
    () => ({
      status,
      isElevated,
      elevatedUntil,
      remainingSeconds,
      requestElevation,
      revokeElevation,
    }),
    [
      status,
      isElevated,
      elevatedUntil,
      remainingSeconds,
      requestElevation,
      revokeElevation,
    ],
  );

  return (
    <ElevationContext.Provider
      value={value}
    >
      {children}
    </ElevationContext.Provider>
  );
}
