import {
  useContext,
} from "react";

import {
  ElevationContext,
} from "../contexts/ElevationContext";

export function useElevation() {
  const context =
    useContext(ElevationContext);

  if (!context) {
    throw new Error(
      "useElevation deve ser utilizado dentro de ElevationProvider.",
    );
  }

  return context;
}
