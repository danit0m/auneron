import {
  createContext,
} from "react";

import type {
  ElevationContextValue,
} from "../types/elevation";

export const ElevationContext =
  createContext<ElevationContextValue | null>(
    null,
  );
