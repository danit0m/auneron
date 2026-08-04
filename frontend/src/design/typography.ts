export const fontFamily = {
  sans: [
    "Inter",
    "Segoe UI",
    "Roboto",
    "Helvetica Neue",
    "Arial",
    "sans-serif",
  ].join(", "),
  mono: [
    "JetBrains Mono",
    "Cascadia Code",
    "SFMono-Regular",
    "Consolas",
    "Liberation Mono",
    "monospace",
  ].join(", "),
} as const;

export const fontSize = {
  xs: "0.75rem",
  sm: "0.875rem",
  md: "1rem",
  lg: "1.125rem",
  xl: "1.25rem",
  "2xl": "1.5rem",
  "3xl": "1.875rem",
  "4xl": "2.25rem",
  "5xl": "3rem",
} as const;

export const fontWeight = {
  regular: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
  extrabold: 800,
} as const;

export const lineHeight = {
  none: 1,
  tight: 1.2,
  snug: 1.35,
  normal: 1.5,
  relaxed: 1.65,
  loose: 1.8,
} as const;

export const letterSpacing = {
  tighter: "-0.04em",
  tight: "-0.02em",
  normal: "0",
  wide: "0.04em",
  wider: "0.08em",
  widest: "0.12em",
} as const;

export const typography = {
  display: {
    fontFamily: fontFamily.sans,
    fontSize: fontSize["4xl"],
    fontWeight: fontWeight.extrabold,
    lineHeight: lineHeight.tight,
    letterSpacing: letterSpacing.tighter,
  },

  heading1: {
    fontFamily: fontFamily.sans,
    fontSize: fontSize["3xl"],
    fontWeight: fontWeight.extrabold,
    lineHeight: lineHeight.tight,
    letterSpacing: letterSpacing.tight,
  },

  heading2: {
    fontFamily: fontFamily.sans,
    fontSize: fontSize["2xl"],
    fontWeight: fontWeight.bold,
    lineHeight: lineHeight.snug,
    letterSpacing: letterSpacing.tight,
  },

  heading3: {
    fontFamily: fontFamily.sans,
    fontSize: fontSize.xl,
    fontWeight: fontWeight.bold,
    lineHeight: lineHeight.snug,
    letterSpacing: letterSpacing.normal,
  },

  bodyLarge: {
    fontFamily: fontFamily.sans,
    fontSize: fontSize.md,
    fontWeight: fontWeight.regular,
    lineHeight: lineHeight.relaxed,
    letterSpacing: letterSpacing.normal,
  },

  body: {
    fontFamily: fontFamily.sans,
    fontSize: fontSize.sm,
    fontWeight: fontWeight.regular,
    lineHeight: lineHeight.normal,
    letterSpacing: letterSpacing.normal,
  },

  bodySmall: {
    fontFamily: fontFamily.sans,
    fontSize: fontSize.xs,
    fontWeight: fontWeight.regular,
    lineHeight: lineHeight.normal,
    letterSpacing: letterSpacing.normal,
  },

  label: {
    fontFamily: fontFamily.sans,
    fontSize: fontSize.xs,
    fontWeight: fontWeight.semibold,
    lineHeight: lineHeight.normal,
    letterSpacing: letterSpacing.wide,
  },

  eyebrow: {
    fontFamily: fontFamily.sans,
    fontSize: fontSize.xs,
    fontWeight: fontWeight.extrabold,
    lineHeight: lineHeight.normal,
    letterSpacing: letterSpacing.widest,
    textTransform: "uppercase",
  },

  code: {
    fontFamily: fontFamily.mono,
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    lineHeight: lineHeight.normal,
    letterSpacing: letterSpacing.normal,
  },
} as const;

export type Typography = typeof typography;
