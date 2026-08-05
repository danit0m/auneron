import type {
  UserRole,
} from "../types/auth";

export const userRoles = [
  "viewer",
  "analyst",
  "manager",
  "executive",
  "administrator",
  "developer",
] as const satisfies readonly UserRole[];

export const roleLabels: Record<
  UserRole,
  string
> = {
  viewer: "Visualizador",
  analyst: "Analista",
  manager: "Gestor",
  executive: "Executivo",
  administrator: "Administrador",
  developer: "Desenvolvedor",
};

export const roleHierarchy: Record<
  UserRole,
  number
> = {
  viewer: 10,
  analyst: 20,
  manager: 30,
  executive: 40,
  administrator: 50,
  developer: 60,
};

export function isUserRole(
  value: unknown,
): value is UserRole {
  return (
    typeof value === "string" &&
    userRoles.includes(
      value as UserRole,
    )
  );
}

export function hasMinimumRole(
  currentRole: UserRole,
  requiredRole: UserRole,
): boolean {
  return (
    roleHierarchy[currentRole] >=
    roleHierarchy[requiredRole]
  );
}
