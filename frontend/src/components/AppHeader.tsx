import { useEffect, useRef, useState, type RefObject } from "react";
import type { AuthUser } from "../lib/api";

/**
 * Hand-styled equivalent of the shared `foldingos-ui` AppHeader (waffle app
 * launcher + profile menu) — this app has no Tailwind/foldingos-ui
 * dependency, so it's built from chat's own existing CSS system instead of
 * the real npm component. Same behavior and (via the shared --bg/--border/
 * --text/--accent palette, which this app's tokens originated) same look.
 */

interface AppDirectoryEntry {
  slug: string;
  name: string;
  url: string;
  description: string;
  icon: string;
}

const IDENTITY_ORIGIN = "https://identity.folding-os.com";

function initials(name: string): string {
  return name
    .split(" ")
    .filter(Boolean)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function useClickOutside(ref: RefObject<HTMLElement | null>, onOutside: () => void) {
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onOutside();
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [ref, onOutside]);
}

function AppLauncher() {
  const [open, setOpen] = useState(false);
  const [apps, setApps] = useState<AppDirectoryEntry[] | null>(null);
  const [failed, setFailed] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setOpen(false));

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && apps === null && !failed) {
      fetch(`${IDENTITY_ORIGIN}/api/apps`)
        .then((res) => {
          if (!res.ok) throw new Error("Failed to load apps");
          return res.json() as Promise<AppDirectoryEntry[]>;
        })
        .then(setApps)
        .catch(() => setFailed(true));
    }
  }

  return (
    <div className="app-launcher" ref={ref}>
      <button type="button" className="app-launcher-btn" aria-label="Apps" onClick={toggle}>
        <i className="codicon codicon-extensions" aria-hidden="true" />
      </button>
      {open && (
        <div className="app-launcher-menu">
          {failed ? (
            <p className="app-launcher-empty">Couldn&apos;t load apps.</p>
          ) : apps === null ? (
            <p className="app-launcher-empty">Loading…</p>
          ) : (
            <div className="app-launcher-grid">
              {apps.map((app) => (
                <a key={app.slug} href={app.url} className="app-launcher-item">
                  <span className="app-launcher-icon">{app.icon}</span>
                  <span>{app.name}</span>
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ProfileMenu({ user, onLogout }: { user: AuthUser; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setOpen(false));
  const onIdentity = typeof window !== "undefined" && window.location.origin === IDENTITY_ORIGIN;
  const displayName = user.name || user.username;

  return (
    <div className="profile-menu" ref={ref}>
      <button type="button" className="profile-menu-btn" aria-label="Account" onClick={() => setOpen((o) => !o)}>
        {user.picture ? (
          <img src={user.picture} alt="" className="profile-avatar-img" />
        ) : (
          <span className="profile-avatar-fallback">{initials(displayName)}</span>
        )}
      </button>
      {open && (
        <div className="profile-menu-dropdown">
          <div className="profile-menu-header">
            <p className="profile-menu-name">{displayName}</p>
            {user.email && <p className="profile-menu-email">{user.email}</p>}
          </div>
          <div className="profile-menu-sep" />
          {!onIdentity && (
            <a href={IDENTITY_ORIGIN} className="profile-menu-item">
              <i className="codicon codicon-organization" aria-hidden="true" />
              Identity
            </a>
          )}
          <button type="button" className="profile-menu-item" onClick={onLogout}>
            <i className="codicon codicon-sign-out" aria-hidden="true" />
            Log out
          </button>
        </div>
      )}
    </div>
  );
}

export default function AppHeader({ user, onLogout }: { user: AuthUser; onLogout: () => void }) {
  return (
    <header className="topbar">
      <div className="brand">
        <AppLauncher />
        <span className="logo" aria-hidden>
          ◈
        </span>
        Qwen Chat
      </div>
      <div className="topbar-right">
        <ProfileMenu user={user} onLogout={onLogout} />
      </div>
    </header>
  );
}
