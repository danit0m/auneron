import type {
  ReactNode,
} from "react";

import {
  useAuth,
} from "../hooks/useAuth";
import type {
  Permission,
} from "../types/auth";

interface PermissionGuardProps {
  permission?: Permission;
  anyPermissions?: readonly Permission[];
  allPermissions?: readonly Permission[];
  fallback?: ReactNode;
  children: ReactNode;
}

export function PermissionGuard({
  permission,
  anyPermissions,
  allPermissions,
  fallback = null,
  children,
}: PermissionGuardProps) {
  const {
    hasPermission,
    hasAnyPermission,
    hasAllPermissions,
  } = useAuth();

  const allowedByPermission =
    !permission ||
    hasPermission(permission);

  const allowedByAny =
    !anyPermissions ||
    anyPermissions.length === 0 ||
    hasAnyPermission(
      anyPermissions,
    );

  const allowedByAll =
    !allPermissions ||
    allPermissions.length === 0 ||
    hasAllPermissions(
      allPermissions,
    );

  if (
    !allowedByPermission ||
    !allowedByAny ||
    !allowedByAll
  ) {
    return fallback;
  }

  return children;
}
