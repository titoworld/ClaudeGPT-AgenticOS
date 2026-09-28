// A logout the server has not confirmed yet (audit A4). The session cookie is HttpOnly:
// only the server can end the session. Until it confirms (204, or 401: there was no
// session), this browser must never open the app on its own, not even after a reload.
// The marker lives in localStorage (every tab, and after the browser restarts) and in
// sessionStorage (this tab, should localStorage be unavailable), and this page also
// keeps it in memory. Any storage access may throw (storage disabled, private mode).

export const LOGOUT_PENDING_KEY = 'aos.logout-pending';

type Area = 'localStorage' | 'sessionStorage';
const AREAS: readonly Area[] = ['localStorage', 'sessionStorage'];

let pendingHere = false;

/** The storage area, or null where even reading `window.localStorage` throws. */
function storage(name: Area): Storage | null {
  try {
    return globalThis[name] ?? null;
  } catch {
    return null;
  }
}

export function markLogoutPending(): void {
  pendingHere = true;
  for (const name of AREAS) {
    try {
      storage(name)?.setItem(LOGOUT_PENDING_KEY, '1');
    } catch {
      // unavailable: the other area and this page's memory still have it
    }
  }
}

/** The server confirmed the logout, or a login ended that session. */
export function clearLogoutPending(): void {
  pendingHere = false;
  for (const name of AREAS) {
    try {
      storage(name)?.removeItem(LOGOUT_PENDING_KEY);
    } catch {
      // unavailable: nothing was stored there either
    }
  }
}

export function isLogoutPending(): boolean {
  if (pendingHere) return true;
  return AREAS.some((name) => {
    try {
      return storage(name)?.getItem(LOGOUT_PENDING_KEY) != null;
    } catch {
      return false;
    }
  });
}
