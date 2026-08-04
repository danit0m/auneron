import {
  colors,
  semanticColors,
} from "./colors";
import {
  duration,
  easing,
  keyframes,
  motion,
  transitions,
} from "./animations";
import { icons } from "./icons";
import {
  radius,
  semanticRadius,
} from "./radius";
import {
  semanticShadows,
  shadows,
} from "./shadows";
import {
  semanticSpacing,
  spacing,
} from "./spacing";
import {
  fontFamily,
  fontSize,
  fontWeight,
  letterSpacing,
  lineHeight,
  typography,
} from "./typography";

export const lightTheme = {
  name: "auneron-light",

  colors,
  semanticColors,

  spacing,
  semanticSpacing,

  radius,
  semanticRadius,

  shadows,
  semanticShadows,

  fontFamily,
  fontSize,
  fontWeight,
  lineHeight,
  letterSpacing,
  typography,

  duration,
  easing,
  transitions,
  keyframes,
  motion,

  icons,
} as const;

export type AuneronTheme = typeof lightTheme;

export const theme = lightTheme;
