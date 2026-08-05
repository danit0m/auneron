import type { SpinnerProps } from "./Spinner.types";
import "./Spinner.css";

const cx = (...classes: Array<string | false | null | undefined>) =>
  classes.filter(Boolean).join(" ");

export function Spinner({
  size = "md",
  label = "Carregando",
  className,
  ...props
}: SpinnerProps) {
  return (
    <span
      className={cx("ui-spinner", `ui-spinner-${size}`, className)}
      role="status"
      aria-label={label}
      {...props}
    >
      <span className="ui-spinner-ring" aria-hidden="true" />
      <span className="ui-visually-hidden">{label}</span>
    </span>
  );
}
