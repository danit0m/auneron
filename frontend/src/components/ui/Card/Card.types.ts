import type { HTMLAttributes, ReactNode } from "react";

export type CardVariant = "default" | "subtle" | "elevated" | "brand" | "success" | "warning" | "danger";
export type CardPadding = "none" | "sm" | "md" | "lg";

export interface CardProps extends HTMLAttributes<HTMLElement> {
  as?: "article" | "section" | "div";
  variant?: CardVariant;
  padding?: CardPadding;
  interactive?: boolean;
  children: ReactNode;
}

export interface CardSectionProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}
