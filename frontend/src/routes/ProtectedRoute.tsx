import type {
  ReactNode,
} from "react";
import {
  Navigate,
  useLocation,
} from "react-router-dom";

import {
  useAuth,
} from "../hooks/useAuth";
import type {
  Permission,
} from "../types/auth";

interface ProtectedRouteProps {
  children: ReactNode;
  permission?: Permission;
  anyPermissions?: readonly Permission[];
  allPermissions?: readonly Permission[];
}

export function ProtectedRoute({
  children,
  permission,
  anyPermissions,
  allPermissions,
}: ProtectedRouteProps) {
  const location = useLocation();

  const {
    isAuthenticated,
    isLoading,
    hasPermission,
    hasAnyPermission,
    hasAllPermissions,
  } = useAuth();

  if (isLoading) {
    return (
      <div className="state-container">
        <div className="loading-spinner" />

        <p>
          Validando acesso...
        </p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <Navigate
        to="/access-denied"
        replace
        state={{
          from: location.pathname,
          reason: "not-authenticated",
        }}
      />
    );
  }

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
    return (
      <Navigate
        to="/access-denied"
        replace
        state={{
          from: location.pathname,
          reason: "insufficient-permission",
        }}
      />
    );
  }

  return children;
}
