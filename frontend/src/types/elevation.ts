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
  isDevelopmentElevation: boolean;
  requestElevation: (
    credential: string,
  ) => Promise<ElevationAttemptResult>;
  revokeElevation: () => void;
}
