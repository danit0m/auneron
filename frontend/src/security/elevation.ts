export function getRemainingSeconds(
  elevatedUntil: string | null,
): number {
  if (!elevatedUntil) {
    return 0;
  }

  const expiration =
    new Date(elevatedUntil).getTime();

  if (
    !Number.isFinite(expiration)
  ) {
    return 0;
  }

  return Math.max(
    0,
    Math.ceil(
      (expiration - Date.now()) /
        1000,
    ),
  );
}

export function isElevationActive(
  elevatedUntil: string | null,
): boolean {
  return (
    getRemainingSeconds(
      elevatedUntil,
    ) > 0
  );
}

export function formatElevationTime(
  remainingSeconds: number,
): string {
  const minutes = Math.floor(
    remainingSeconds / 60,
  );

  const seconds =
    remainingSeconds % 60;

  return [
    String(minutes).padStart(
      2,
      "0",
    ),
    String(seconds).padStart(
      2,
      "0",
    ),
  ].join(":");
}
