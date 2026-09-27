// Tiny hash router: #/ (new conversation), #/c/<id>, #/tauler (dashboard).
// Hash routing keeps deep links and the back button working without any
// server-side routing for the SPA.

export type Route = { name: 'chat'; id: number | null } | { name: 'dashboard' };

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, '');
  if (path === '/tauler') return { name: 'dashboard' };
  const m = /^\/c\/(\d+)$/.exec(path);
  if (m) return { name: 'chat', id: Number(m[1]) };
  return { name: 'chat', id: null };
}

export function routeHash(route: Route): string {
  if (route.name === 'dashboard') return '#/tauler';
  return route.id == null ? '#/' : `#/c/${route.id}`;
}

class Router {
  route: Route = $state({ name: 'chat', id: null });

  constructor() {
    if (typeof window === 'undefined') return;
    this.route = parseHash(location.hash);
    window.addEventListener('hashchange', () => {
      this.route = parseHash(location.hash);
    });
  }

  go(route: Route): void {
    const hash = routeHash(route);
    if (location.hash === hash || (hash === '#/' && location.hash === '')) {
      this.route = route;
      return;
    }
    location.hash = hash; // hashchange updates `route`
  }

  /** Change the URL without a history entry (e.g. a new conversation got its id). */
  replace(route: Route): void {
    history.replaceState(history.state, '', routeHash(route));
    this.route = route;
  }
}

export const router = new Router();
