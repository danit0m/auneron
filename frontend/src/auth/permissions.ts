import type {
  Permission,
  UserRole,
} from "../types/auth";

const viewerPermissions = [
  "dashboard.view",
  "clients.view",
] as const satisfies readonly Permission[];

const analystPermissions = [
  ...viewerPermissions,
  "clients.manage",
  "imports.execute",
  "brain.view",
  "brain.manage",
  "work:create",
  "work:assess_escalation",
] as const satisfies readonly Permission[];

const managerPermissions = [
  ...analystPermissions,
  "executive.view",
  "approval:read",
  "approval:decide",
  "work:read",
  "skill:execute",
  "skill:execute_mutating",
  "account:contact",
] as const satisfies readonly Permission[];

const executivePermissions = [
  ...managerPermissions,
] as const satisfies readonly Permission[];

const administratorPermissions = [
  ...executivePermissions,
  "administration.ai-operations",
] as const satisfies readonly Permission[];

const developerPermissions = [
  ...administratorPermissions,
  "developer.ui-showcase",
] as const satisfies readonly Permission[];

export const rolePermissions: Record<
  UserRole,
  readonly Permission[]
> = {
  viewer: viewerPermissions,
  analyst: analystPermissions,
  manager: managerPermissions,
  executive: executivePermissions,
  administrator:
    administratorPermissions,
  developer: developerPermissions,
};

export const privilegedPermissions = [
  "administration.ai-operations",
  "developer.ui-showcase",
] as const satisfies readonly Permission[];

export function getPermissionsForRole(
  role: UserRole,
): readonly Permission[] {
  return rolePermissions[role];
}

/** Cadastro de cliente (botao "Novo cliente") exige clients.manage. */
export function podeCadastrarCliente(
  hasPermission: (permission: Permission) => boolean,
): boolean {
  return hasPermission("clients.manage");
}

/** Registrar contato/promessa em Recebiveis exige account:contact. */
export function podeRegistrarContato(
  hasPermission: (permission: Permission) => boolean,
): boolean {
  return hasPermission("account:contact");
}
