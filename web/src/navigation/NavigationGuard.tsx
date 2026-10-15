import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { useBlocker } from "react-router";
import { useConfirm } from "../components/tasks/ConfirmDialog";
import { clearRoutineContinuation } from "../auth/browser-storage";
import { decideNavigation } from "./decisions";
import type { NavigationDecision } from "./decisions";
const Context = createContext<
  (id: string, message: string | null, decide?: () => Promise<boolean>) => void
>(() => {});
export function NavigationGuard({ children }: { children: ReactNode }) {
  const blockers = useRef(new Map<string, NavigationDecision>()),
    [dirty, setDirty] = useState(false),
    pending = useRef(false),
    approvedDocumentNavigation = useRef(false),
    routerDecision = useRef(false);
  const { confirm } = useConfirm();
  const register = useCallback(
    (id: string, message: string | null, decide?: () => Promise<boolean>) => {
      if (message)
        blockers.current.set(id, { message, ...(decide ? { decide } : {}) });
      else blockers.current.delete(id);
      setDirty(blockers.current.size > 0);
    },
    [],
  );
  const ask = useCallback(async () => {
    if (pending.current) return false;
    pending.current = true;
    try {
      return await decideNavigation([...blockers.current.values()], (message) =>
        confirm({
          title: "Leave this page?",
          message,
          confirmLabel: "Leave page",
          cancelLabel: "Stay",
          danger: true,
        }),
      );
    } finally {
      pending.current = false;
    }
  }, [confirm]);
  const blocked = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      blockers.current.size > 0 &&
      currentLocation.pathname !== nextLocation.pathname,
  );
  useEffect(() => {
    if (blocked.state === "blocked" && !routerDecision.current) {
      routerDecision.current = true;
      void ask()
        .then((leave) => {
          if (leave) blocked.proceed();
          else blocked.reset();
        })
        .finally(() => {
          routerDecision.current = false;
        });
    }
  }, [blocked, ask]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (approvedDocumentNavigation.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    const pageShown = () => {
      approvedDocumentNavigation.current = false;
    };
    const click = (event: MouseEvent) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const anchor = (event.target as Element).closest<HTMLAnchorElement>(
        "a[href]",
      );
      if (
        !anchor ||
        anchor.download ||
        (anchor.target && anchor.target !== "_self")
      )
        return;
      const destination = new URL(anchor.href, location.href);
      if (
        destination.origin === location.origin &&
        destination.pathname === location.pathname &&
        destination.search === location.search
      )
        return;
      event.preventDefault();
      void ask().then((leave) => {
        if (leave) {
          approvedDocumentNavigation.current = true;
          location.assign(destination.href);
        }
      });
    };
    const submit = (event: SubmitEvent) => {
      const form = event.target;
      if (
        event.defaultPrevented ||
        !(form instanceof HTMLFormElement) ||
        !form.hasAttribute("data-navigation")
      )
        return;
      const destination = new URL(form.action, location.href);
      const logout = form.method.toLowerCase() === "post" &&
        destination.origin === location.origin && destination.pathname === "/logout";
      if (!dirty) {
        if (logout) clearRoutineContinuation();
        return;
      }
      event.preventDefault();
      void ask().then((leave) => {
        if (leave) {
          approvedDocumentNavigation.current = true;
          if (logout) clearRoutineContinuation();
          HTMLFormElement.prototype.submit.call(form);
        }
      });
    };
    if (dirty) {
      window.addEventListener("beforeunload", unload);
      window.addEventListener("pageshow", pageShown);
      document.addEventListener("click", click);
    }
    document.addEventListener("submit", submit);
    return () => {
      window.removeEventListener("beforeunload", unload);
      window.removeEventListener("pageshow", pageShown);
      document.removeEventListener("click", click);
      document.removeEventListener("submit", submit);
    };
  }, [dirty, ask]);
  return <Context value={register}>{children}</Context>;
}
export function useNavigationBlocker(
  id: string,
  active: boolean,
  message: string,
  decide?: () => Promise<boolean>,
) {
  const register = useContext(Context);
  useEffect(() => {
    register(id, active ? message : null, decide);
    return () => register(id, null);
  }, [register, id, active, message, decide]);
}
