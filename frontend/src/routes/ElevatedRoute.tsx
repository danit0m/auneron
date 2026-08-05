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
    hasPermission,
  } = useAuth();

  const {
    isElevated,
  } = useElevation();

  if (!isAuthenticated) {
    return (
      <Navigate
        to="/access-denied"
        replace
        state={{
          from: location.pathname,
          reason:
            "not-authenticated",
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
