import type { BadgeProps } from "./Badge.types";
import "./Badge.css";

const cx = (...classes: Array<string | false | null | undefined>) =>
  classes.filter(Boolean).join(" ");

export function Badge({
  variant = "neutral",
  size = "md",
  dot = false,
  icon,
  className,
  children,
  ...props
}: BadgeProps) {
  return (
    <span className={cx("ui-badge", `ui-badge-${variant}`, `ui-badge-${size}`, className)} {...props}>
      {dot ? <span className="ui-badge-dot" aria-hidden="true" /> : null}
      {icon ? <span className="ui-badge-icon">{icon}</span> : null}
      <span className="ui-badge-label">{children}</span>
    </span>
  );
}
