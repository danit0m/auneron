import {
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";

import {
  ThemeContext,
} from "../contexts/ThemeContext";
import {
  AUNERON_THEME_STORAGE_KEY,
  isThemePreference,
  type ResolvedTheme,
  type ThemePreference,
} from "../types/theme";

interface ThemeProviderProps {
  children: ReactNode;
  defaultTheme?: ThemePreference;
  storageKey?: string;
}

const SYSTEM_DARK_QUERY =
  "(prefers-color-scheme: dark)";

function obterTemaDoSistema(): ResolvedTheme {
  if (
    typeof window !== "undefined" &&
    window.matchMedia(SYSTEM_DARK_QUERY)
      .matches
  ) {
    return "dark";
  }

  return "light";
}

function obterPreferenciaInicial(
  defaultTheme: ThemePreference,
  storageKey: string,
): ThemePreference {
  if (typeof window === "undefined") {
    return defaultTheme;
  }

  try {
    const valorSalvo =
      window.localStorage.getItem(
        storageKey,
      );

    if (
      isThemePreference(valorSalvo)
    ) {
      return valorSalvo;
    }
  } catch {
    // O tema padrão continua válido quando
    // o armazenamento está indisponível.
  }

  return defaultTheme;
}

function resolverTema(
  preference: ThemePreference,
  systemTheme: ResolvedTheme,
): ResolvedTheme {
  return preference === "system"
    ? systemTheme
    : preference;
}

export function ThemeProvider({
  children,
  defaultTheme = "system",
  storageKey =
    AUNERON_THEME_STORAGE_KEY,
}: ThemeProviderProps) {
  const [
    preference,
    setPreference,
  ] = useState<ThemePreference>(() =>
    obterPreferenciaInicial(
      defaultTheme,
      storageKey,
    ),
  );

  const [
    systemTheme,
    setSystemTheme,
  ] = useState<ResolvedTheme>(
    obterTemaDoSistema,
  );

  const resolvedTheme = resolverTema(
    preference,
    systemTheme,
  );

  useEffect(() => {
    const mediaQuery =
      window.matchMedia(
        SYSTEM_DARK_QUERY,
      );

    function atualizarTemaDoSistema(
      event: MediaQueryListEvent,
    ) {
      setSystemTheme(
        event.matches
          ? "dark"
          : "light",
      );
    }

    mediaQuery.addEventListener(
      "change",
      atualizarTemaDoSistema,
    );

    return () => {
      mediaQuery.removeEventListener(
        "change",
        atualizarTemaDoSistema,
      );
    };
  }, []);

  useLayoutEffect(() => {
    const root =
      document.documentElement;

    root.dataset.theme =
      resolvedTheme;

    root.dataset.themePreference =
      preference;

    root.style.colorScheme =
      resolvedTheme;

    root.classList.toggle(
      "dark",
      resolvedTheme === "dark",
    );
  }, [
    preference,
    resolvedTheme,
  ]);

  const setTheme = useCallback(
    (
      novaPreferencia:
        ThemePreference,
    ) => {
      setPreference(
        novaPreferencia,
      );

      try {
        window.localStorage.setItem(
          storageKey,
          novaPreferencia,
        );
      } catch {
        // A preferência continua funcionando
        // durante a sessão atual.
      }
    },
    [storageKey],
  );

  const toggleTheme = useCallback(
    () => {
      setTheme(
        resolvedTheme === "dark"
          ? "light"
          : "dark",
      );
    },
    [
      resolvedTheme,
      setTheme,
    ],
  );

  const resetTheme = useCallback(
    () => {
      setPreference(defaultTheme);

      try {
        window.localStorage.removeItem(
          storageKey,
        );
      } catch {
        // Não impede a restauração em memória.
      }
    },
    [
      defaultTheme,
      storageKey,
    ],
  );

  const value = useMemo(
    () => ({
      preference,
      resolvedTheme,
      isDark:
        resolvedTheme === "dark",
      setTheme,
      toggleTheme,
      resetTheme,
    }),
    [
      preference,
      resolvedTheme,
      setTheme,
      toggleTheme,
      resetTheme,
    ],
  );

  return (
    <ThemeContext.Provider
      value={value}
    >
      {children}
    </ThemeContext.Provider>
  );
}
