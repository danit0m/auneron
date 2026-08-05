import type { HTMLAttributes, ReactNode } from "react";

export type BadgeVariant = "neutral" | "brand" | "info" | "success" | "warning" | "danger";
export type BadgeSize = "sm" | "md";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  size?: BadgeSize;
  dot?: boolean;
  icon?: ReactNode;
}
