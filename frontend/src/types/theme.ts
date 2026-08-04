export const AUNERON_THEME_STORAGE_KEY =
  "auneron.theme.preference";

export const themePreferences = [
  "system",
  "light",
  "dark",
] as const;

export type ThemePreference =
  (typeof themePreferences)[number];

export type ResolvedTheme =
  Exclude<ThemePreference, "system">;

export interface ThemeContextValue {
  preference: ThemePreference;
  resolvedTheme: ResolvedTheme;
  isDark: boolean;
  setTheme: (
    preference: ThemePreference,
  ) => void;
  toggleTheme: () => void;
  resetTheme: () => void;
}

export function isThemePreference(
  value: unknown,
): value is ThemePreference {
  return (
    typeof value === "string" &&
    themePreferences.includes(
      value as ThemePreference,
    )
  );
}
