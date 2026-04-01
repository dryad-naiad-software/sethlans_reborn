import { CanActivateFn } from '@angular/router';

/**
 * Stubbed auth guard that always allows access.
 * When authentication is implemented, this will check for valid credentials
 * and redirect to /login if not authenticated.
 */
export const authGuard: CanActivateFn = () => {
  // TODO: Check authentication state when auth is enabled
  return true;
};
