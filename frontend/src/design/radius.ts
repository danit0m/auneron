export const radius = {
  none: "0",
  xs: "0.25rem",
  sm: "0.375rem",
  md: "0.5rem",
  lg: "0.75rem",
  xl: "1rem",
  "2xl": "1.25rem",
  "3xl": "1.5rem",
  full: "9999px",
} as const;

export const semanticRadius = {
  input: radius.lg,
  button: radius.lg,
  card: radius.xl,
  cardLarge: radius["2xl"],
  modal: radius["2xl"],
  badge: radius.full,
  avatar: radius.full,
  icon: radius.lg,
} as const;

export type Radius = typeof radius;
export type SemanticRadius = typeof semanticRadius;
