import { forwardRef, useId } from "react";
import type { InputProps } from "./Input.types";
import "./Input.css";

const cx = (...classes: Array<string | false | null | undefined>) =>
  classes.filter(Boolean).join(" ");

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    id,
    label,
    helperText,
    error,
    startIcon,
    endIcon,
    inputSize = "md",
    fullWidth = true,
    className,
    disabled,
    required,
    "aria-describedby": ariaDescribedBy,
    ...props
  },
  ref,
) {
  const generatedId = useId();
  const inputId = id ?? `ui-input-${generatedId}`;
  const helperId = `${inputId}-helper`;
  const errorId = `${inputId}-error`;
  const describedBy = [
    ariaDescribedBy,
    helperText && helperId,
    error && errorId,
  ].filter(Boolean).join(" ") || undefined;

  return (
    <div className={cx(
      "ui-input-field",
      fullWidth && "ui-input-field-full-width",
      disabled && "ui-input-field-disabled",
      error && "ui-input-field-error",
    )}>
      {label ? (
        <label className="ui-input-label" htmlFor={inputId}>
          {label}
          {required ? <span className="ui-input-required" aria-hidden="true">*</span> : null}
        </label>
      ) : null}

      <div className={cx("ui-input-control", `ui-input-control-${inputSize}`)}>
        {startIcon ? <span className="ui-input-icon" aria-hidden="true">{startIcon}</span> : null}
        <input
          ref={ref}
          id={inputId}
          className={cx("ui-input", className)}
          disabled={disabled}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          {...props}
        />
        {endIcon ? <span className="ui-input-icon" aria-hidden="true">{endIcon}</span> : null}
      </div>

      {error ? (
        <span id={errorId} className="ui-input-message ui-input-message-error" role="alert">{error}</span>
      ) : helperText ? (
        <span id={helperId} className="ui-input-message">{helperText}</span>
      ) : null}
    </div>
  );
});
