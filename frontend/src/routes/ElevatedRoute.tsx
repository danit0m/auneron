import type {
  ReactNode,
} from "react";
import {
  Navigate,
  useLocation,
  useNavigate,
} from "react-router-dom";

import {
  ElevatedAccessModal,
} from "../components/security/ElevatedAccessModal";
import {
  useAuth,
} from "../hooks/useAuth";
import {
  useElevation,
} from "../hooks/useElevation";
import type {
  Permission,
} from "../types/auth";

interface ElevatedRouteProps {
  children: ReactNode;
  permission: Permission;
  resourceLabel: string;
}

export function ElevatedRoute({
  children,
  permission,
  resourceLabel,
}: ElevatedRouteProps) {
  const location =
    useLocation();

  const navigate =
    useNavigate();

  const {
    isAuthenticated,
    isLoading,
    hasPermission,
  } = useAuth();

  const {
    isElevated,
  } = useElevation();

  if (isLoading) {
    return (
      <div className="state-container">
        <div className="loading-spinner" />

        <p>
          Validando sessão...
        </p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <Navigate
        to="/login"
        replace
        state={{
          from:
            location.pathname +
            location.search,
        }}
      />
    );
  }

  if (
    !hasPermission(permission)
  ) {
    return (
      <Navigate
        to="/access-denied"
        replace
        state={{
          from: location.pathname,
          reason:
            "insufficient-permission",
        }}
      />
    );
  }

  if (isElevated) {
    return children;
  }

  return (
    <ElevatedAccessModal
      open
      resourceLabel={
        resourceLabel
      }
      onCancel={() =>
        navigate("/", {
          replace: true,
        })
      }
    />
  );
}
