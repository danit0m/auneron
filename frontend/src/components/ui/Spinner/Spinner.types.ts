import type { HTMLAttributes } from "react";

export type SpinnerSize = "sm" | "md" | "lg";

export interface SpinnerProps extends HTMLAttributes<HTMLSpanElement> {
  size?: SpinnerSize;
  label?: string;
}
