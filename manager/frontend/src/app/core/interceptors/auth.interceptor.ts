import { HttpInterceptorFn } from '@angular/common/http';

/**
 * Stubbed auth interceptor. When authentication is implemented,
 * this will attach auth tokens to outgoing requests.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  // TODO: Attach auth token when authentication is enabled
  return next(req);
};
