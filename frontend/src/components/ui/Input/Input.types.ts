import type { InputHTMLAttributes, ReactNode } from "react";

export type InputSize = "sm" | "md" | "lg";

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  label?: string;
  helperText?: string;
  error?: string;
  startIcon?: ReactNode;
  endIcon?: ReactNode;
  inputSize?: InputSize;
  fullWidth?: boolean;
}
