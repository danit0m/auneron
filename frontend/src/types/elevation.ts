export type ElevationStatus =
  | "idle"
  | "validating"
  | "elevated"
  | "expired"
  | "unavailable";

export interface ElevationAttemptResult {
  success: boolean;
  message: string;
}

export interface ElevationContextValue {
  status: ElevationStatus;
  isElevated: boolean;
  elevatedUntil: string | null;
  remainingSeconds: number;
  requestElevation: (
    password: string,
  ) => Promise<ElevationAttemptResult>;
  revokeElevation: () => Promise<void>;
}
