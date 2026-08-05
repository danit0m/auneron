import {
  getPermissionsForRole,
} from "./permissions";
import type {
  AuthUser,
  Permission,
} from "../types/auth";

export function canAccess(
  user: AuthUser | null,
  permission: Permission,
): boolean {
  if (!user || !user.active) {
    return false;
  }

  return getPermissionsForRole(
    user.role,
  ).includes(permission);
}

export function canAccessAny(
  user: AuthUser | null,
  permissions: readonly Permission[],
): boolean {
  return permissions.some(
    (permission) =>
      canAccess(user, permission),
  );
}

export function canAccessAll(
  user: AuthUser | null,
  permissions: readonly Permission[],
): boolean {
  return permissions.every(
    (permission) =>
      canAccess(user, permission),
  );
}
