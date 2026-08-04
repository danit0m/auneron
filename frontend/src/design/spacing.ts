export const spacing = {
  0: "0",
  px: "1px",
  0.5: "0.125rem",
  1: "0.25rem",
  1.5: "0.375rem",
  2: "0.5rem",
  2.5: "0.625rem",
  3: "0.75rem",
  3.5: "0.875rem",
  4: "1rem",
  5: "1.25rem",
  6: "1.5rem",
  7: "1.75rem",
  8: "2rem",
  9: "2.25rem",
  10: "2.5rem",
  12: "3rem",
  14: "3.5rem",
  16: "4rem",
  20: "5rem",
  24: "6rem",
  28: "7rem",
  32: "8rem",
  36: "9rem",
  40: "10rem",
  48: "12rem",
  56: "14rem",
  64: "16rem",
} as const;

export const semanticSpacing = {
  page: {
    mobile: spacing[4],
    tablet: spacing[6],
    desktop: spacing[8],
  },

  section: {
    compact: spacing[3],
    default: spacing[5],
    spacious: spacing[8],
  },

  card: {
    compact: spacing[3],
    default: spacing[5],
    spacious: spacing[6],
  },

  control: {
    xSmall: spacing[2],
    small: spacing[2.5],
    medium: spacing[3],
    large: spacing[4],
  },

  layout: {
    sidebarWidth: "16rem",
    sidebarCollapsedWidth: "4.5rem",
    headerHeight: "4.5rem",
    contentMaxWidth: "100rem",
  },
} as const;

export type Spacing = typeof spacing;
export type SemanticSpacing = typeof semanticSpacing;
