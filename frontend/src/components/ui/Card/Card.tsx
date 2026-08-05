import type { ElementType } from "react";
import type { CardProps, CardSectionProps } from "./Card.types";
import "./Card.css";

const cx = (...classes: Array<string | false | null | undefined>) =>
  classes.filter(Boolean).join(" ");

export function Card({
  as = "article",
  variant = "default",
  padding = "md",
  interactive = false,
  className,
  children,
  ...props
}: CardProps) {
  const Component = as as ElementType;
  return (
    <Component
      className={cx(
        "ui-card",
        `ui-card-${variant}`,
        `ui-card-padding-${padding}`,
        interactive && "ui-card-interactive",
        className,
      )}
      {...props}
    >
      {children}
    </Component>
  );
}

export function CardHeader({ className, children, ...props }: CardSectionProps) {
  return <div className={cx("ui-card-header", className)} {...props}>{children}</div>;
}

export function CardContent({ className, children, ...props }: CardSectionProps) {
  return <div className={cx("ui-card-content", className)} {...props}>{children}</div>;
}

export function CardFooter({ className, children, ...props }: CardSectionProps) {
  return <div className={cx("ui-card-footer", className)} {...props}>{children}</div>;
}
