import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  createElevationExpiration,
  ELEVATION_SESSION_KEY,
  getRemainingSeconds,
  isElevationActive,
} from "../security/elevation";
import {
  ElevationContext,
} from "../contexts/ElevationContext";
import type {
  ElevationAttemptResult,
  ElevationStatus,
} from "../types/elevation";

interface ElevationProviderProps {
  children: ReactNode;
}

function getStoredElevation(): string | null {
  if (
    typeof window === "undefined"
  ) {
    return null;
  }

  try {
    const storedValue =
      window.sessionStorage.getItem(
        ELEVATION_SESSION_KEY,
      );

    return isElevationActive(
      storedValue,
    )
      ? storedValue
      : null;
  } catch {
    return null;
  }
}

function getDevelopmentCode():
  | string
  | null {
  const configuredCode =
    import.meta.env
      .VITE_ELEVATED_DEV_CODE;

  if (
    typeof configuredCode !==
      "string" ||
    configuredCode.trim().length ===
      0
  ) {
    return null;
  }

  return configuredCode.trim();
}

export function ElevationProvider({
  children,
}: ElevationProviderProps) {
  const [
    elevatedUntil,
    setElevatedUntil,
  ] = useState<string | null>(
    getStoredElevation,
  );

  const [
    status,
    setStatus,
  ] = useState<ElevationStatus>(
    elevatedUntil
      ? "elevated"
      : "idle",
  );

  const [
    remainingSeconds,
    setRemainingSeconds,
  ] = useState(() =>
    getRemainingSeconds(
      elevatedUntil,
    ),
  );

  const isDevelopmentElevation =
    import.meta.env.DEV;

  useEffect(() => {
    const intervalId =
      window.setInterval(() => {
        const remaining =
          getRemainingSeconds(
            elevatedUntil,
          );

        setRemainingSeconds(
          remaining,
        );

        if (
          elevatedUntil &&
          remaining === 0
        ) {
          setElevatedUntil(null);
          setStatus("expired");

          try {
            window.sessionStorage.removeItem(
              ELEVATION_SESSION_KEY,
            );
          } catch {
            // A expiração em memória continua válida.
          }
        }
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
        credential: string,
      ): Promise<ElevationAttemptResult> => {
        setStatus("validating");

        /*
          Segurança fail-closed:
          enquanto o backend ainda não possui
          POST /auth/elevate, a elevação real de
          produção permanece indisponível.
        */
        if (
          !isDevelopmentElevation
        ) {
          setStatus("unavailable");

          return {
            success: false,
            message:
              "A validação elevada no backend ainda não está disponível.",
          };
        }

        const developmentCode =
          getDevelopmentCode();

        if (!developmentCode) {
          setStatus("unavailable");

          return {
            success: false,
            message:
              "Configure VITE_ELEVATED_DEV_CODE no arquivo .env.local para utilizar a elevação em desenvolvimento.",
          };
        }

        await new Promise<void>(
          (resolve) => {
            window.setTimeout(
              resolve,
              450,
            );
          },
        );

        if (
          credential.trim() !==
          developmentCode
        ) {
          setStatus("idle");

          return {
            success: false,
            message:
              "Credencial elevada inválida.",
          };
        }

        const expiration =
          createElevationExpiration();

        setElevatedUntil(
          expiration,
        );
        setRemainingSeconds(
          getRemainingSeconds(
            expiration,
          ),
        );
        setStatus("elevated");

        try {
          window.sessionStorage.setItem(
            ELEVATION_SESSION_KEY,
            expiration,
          );
        } catch {
          // A elevação continua válida
          // durante a execução atual.
        }

        return {
          success: true,
          message:
            "Credencial elevada validada.",
        };
      },
      [isDevelopmentElevation],
    );

  const revokeElevation =
    useCallback(() => {
      setElevatedUntil(null);
      setRemainingSeconds(0);
      setStatus("idle");

      try {
        window.sessionStorage.removeItem(
          ELEVATION_SESSION_KEY,
        );
      } catch {
        // A revogação em memória continua válida.
      }
    }, []);

  const isElevated =
    status === "elevated" &&
    remainingSeconds > 0;

  const value = useMemo(
    () => ({
      status,
      isElevated,
      elevatedUntil,
      remainingSeconds,
      isDevelopmentElevation,
      requestElevation,
      revokeElevation,
    }),
    [
      status,
      isElevated,
      elevatedUntil,
      remainingSeconds,
      isDevelopmentElevation,
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
