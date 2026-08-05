import { LoaderCircle } from "lucide-react";
import type { ButtonProps } from "./Button.types";
import "./Button.css";

const cx = (...classes: Array<string | false | null | undefined>) =>
  classes.filter(Boolean).join(" ");

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  fullWidth = false,
  startIcon,
  endIcon,
  className,
  children,
  disabled,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        "ui-button",
        `ui-button-${variant}`,
        `ui-button-${size}`,
        fullWidth && "ui-button-full-width",
        loading && "ui-button-loading",
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading}
      {...props}
    >
      {loading ? (
        <LoaderCircle size={18} className="ui-button-spinner" aria-hidden="true" />
      ) : startIcon ? (
        <span className="ui-button-icon">{startIcon}</span>
      ) : null}

      <span className="ui-button-label">{children}</span>

      {!loading && endIcon ? (
        <span className="ui-button-icon">{endIcon}</span>
      ) : null}
    </button>
  );
}
