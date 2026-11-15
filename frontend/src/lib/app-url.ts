/** URLs owned by this application must not depend on root-level PBX aliases. */
const base = (import.meta.env.BASE_URL || '/').replace(/\/$/, '');

export function appPath(path: string): string {
  if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(path)) return path;
  const relative = '/' + path.replace(/^\/+/, '');
  if (!base || relative === base || relative.startsWith(base + '/') ||
      relative.startsWith(base + '?') || relative.startsWith(base + '#')) return relative;
  return base + relative;
}

/** Preserve Request bodies/signals while rebasing only this origin's API paths. */
export function apiRequestTarget(input: RequestInfo | URL): RequestInfo | URL {
  if (typeof input === 'string' && /^api(?:\/|\?|#|$)/.test(input)) return appPath(input);
  const value = input instanceof Request ? input.url : String(input);
  const url = new URL(value, window.location.href);
  if (url.origin !== window.location.origin ||
      (url.pathname !== '/api' && !url.pathname.startsWith('/api/'))) return input;
  url.pathname = appPath(url.pathname);
  if (input instanceof Request) return new Request(url, input);
  return typeof input === 'string' && input.startsWith('/')
    ? url.pathname + url.search + url.hash : url;
}
