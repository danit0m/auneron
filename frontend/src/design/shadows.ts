export const shadows = {
  none: "none",
  xs: "0 1px 2px rgb(15 23 42 / 4%)",
  sm: "0 2px 6px rgb(15 23 42 / 6%)",
  md: "0 8px 22px rgb(15 23 42 / 8%)",
  lg: "0 14px 34px rgb(15 23 42 / 10%)",
  xl: "0 22px 52px rgb(15 23 42 / 14%)",
  "2xl": "0 30px 80px rgb(15 23 42 / 22%)",

  brand: "0 12px 28px rgb(124 58 237 / 20%)",
  primary: "0 12px 28px rgb(37 99 235 / 20%)",
  success: "0 12px 28px rgb(5 150 105 / 18%)",
  warning: "0 12px 28px rgb(217 119 6 / 18%)",
  danger: "0 12px 28px rgb(220 38 38 / 20%)",

  inset: "inset 0 1px 2px rgb(15 23 42 / 6%)",
  focus: "0 0 0 3px rgb(37 99 235 / 18%)",
} as const;

export const semanticShadows = {
  card: shadows.sm,
  cardHover: shadows.md,
  floating: shadows.lg,
  modal: shadows["2xl"],
  dropdown: shadows.lg,
  buttonPrimary: shadows.primary,
  buttonDanger: shadows.danger,
  focus: shadows.focus,
} as const;

export type Shadows = typeof shadows;
export type SemanticShadows = typeof semanticShadows;
