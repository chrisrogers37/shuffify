import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router";
const destinations = [
  ["Library", "/library"],
  ["Workshop", "/workshop"],
  ["Routines", "/routines"],
  ["Activity", "/activity"],
  ["Settings", "/settings"],
] as const;
function Brand() {
  return (
    <Link className="sh-brand" to="/" aria-label="Shuffify home">
      <img
        className="sh-brand-mark"
        src="/images/favicon.svg"
        width="32"
        height="32"
        alt=""
      />
      shuffify
    </Link>
  );
}
export function Navigation() {
  const menu = useRef<HTMLDetailsElement>(null),
    location = useLocation();
  useEffect(() => {
    if (menu.current) menu.current.open = false;
  }, [location]);
  const links = (
    <ul className="sh-app-links">
      {destinations.map(([name, path]) => (
        <li key={path}>
          <NavLink to={path}>{name}</NavLink>
        </li>
      ))}
    </ul>
  );
  return (
    <nav className="sh-app-nav" aria-label="Main navigation">
      <div className="sh-container">
        <div className="sh-app-nav-desktop">
          <Brand />
          {links}
        </div>
        <details ref={menu} className="sh-app-menu">
          <summary>
            <span className="sh-brand" aria-hidden="true">
              shuffify
            </span>
            <span className="sh-app-current">
              {
                destinations.find(([, path]) =>
                  location.pathname.startsWith(path),
                )?.[0]
              }
            </span>
            <span className="sh-app-menu-label">
              Menu <span aria-hidden="true">⌄</span>
            </span>
          </summary>
          {links}
        </details>
      </div>
    </nav>
  );
}
export function Footer() {
  return (
    <footer className="sh-footer">
      <div className="sh-container sh-footer-inner">
        <div>
          <p>Shuffify is an independent app, unaffiliated with Spotify.</p>
          <p>© {new Date().getFullYear()} Shuffify. All rights reserved.</p>
        </div>
        <nav className="sh-nav-links" aria-label="Legal navigation">
          <Link to="/terms">Terms of Service</Link>
          <Link to="/privacy">Privacy Policy</Link>
        </nav>
      </div>
    </footer>
  );
}
function RouteFocus() {
  const location = useLocation();
  useEffect(() => {
    const main = document.querySelector<HTMLElement>("#main-content");
    if (!main) return;
    let lastHeading: HTMLElement | null = null;
    const apply = () => {
      const heading = Array.from(main.querySelectorAll<HTMLElement>("h1")).find(
        (node) => !node.closest("[hidden],[inert]"),
      );
      if (!heading || heading === lastHeading) return;
      lastHeading = heading;
      document.title = `${heading.textContent ?? "Shuffify"} · Shuffify`;
      heading.focus({ preventScroll: true });
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(main, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["hidden", "inert"],
    });
    return () => observer.disconnect();
  }, [location.pathname]);
  return null;
}
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="sh-shell sh-app-shell">
      <a className="sh-skip-link" href="#main-content">
        Skip to main content
      </a>
      <Navigation />
      <main id="main-content" tabIndex={-1}>
        {children}
      </main>
      <Footer />
      <RouteFocus />
    </div>
  );
}
export function PublicShell({ children }: { children: ReactNode }) {
  return (
    <div className="sh-shell sh-public-shell">
      <a className="sh-skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="sh-public-nav">
        <div className="sh-container sh-nav-inner">
          <Brand />
          <nav className="sh-nav-links" aria-label="Public navigation">
            <Link to="/terms">Terms</Link>
            <Link to="/privacy">Privacy</Link>
          </nav>
        </div>
      </header>
      <main id="main-content" tabIndex={-1}>
        {children}
      </main>
      <Footer />
      <RouteFocus />
    </div>
  );
}
