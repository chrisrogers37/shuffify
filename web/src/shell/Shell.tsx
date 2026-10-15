import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigationType } from "react-router";
import { copyrightYear, publicPage } from "../pages/public/meta";
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
          <p>© {copyrightYear} Shuffify. All rights reserved.</p>
        </div>
        <nav className="sh-nav-links" aria-label="Legal navigation">
          <NavLink to="/terms" end>
            Terms of Service
          </NavLink>
          <NavLink to="/privacy" end>
            Privacy Policy
          </NavLink>
        </nav>
      </div>
    </footer>
  );
}
function RouteFocus() {
  const location = useLocation();
  const navigationType = useNavigationType();
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
      document.title =
        publicPage(location.pathname)?.title ??
        `${heading.textContent ?? "Shuffify"} · Shuffify`;
      let fragment: HTMLElement | null = null;
      try {
        if (location.hash)
          fragment = document.getElementById(
            decodeURIComponent(location.hash.slice(1)),
          );
      } catch {
        /* Malformed fragment input falls back to the route heading. */
      }
      if (fragment && !fragment.closest("[hidden],[inert]")) {
        if (!fragment.hasAttribute("tabindex")) fragment.tabIndex = -1;
        fragment.focus({ preventScroll: true });
        fragment.scrollIntoView({ behavior: "instant" });
      } else {
        heading.focus({ preventScroll: true });
        // Native reload/back history keeps its scroll restoration. New route
        // destinations begin at their heading rather than a previous page's footer.
        if (navigationType !== "POP")
          window.scrollTo({ top: 0, behavior: "instant" });
      }
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
  }, [location.pathname, location.hash, navigationType]);
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
            <NavLink to="/terms" end>
              Terms
            </NavLink>
            <NavLink to="/privacy" end>
              Privacy
            </NavLink>
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
