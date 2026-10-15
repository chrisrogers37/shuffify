import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import type { ReactNode } from "react";
import type { ApiClient } from "../api/client";
import type { AuthNotice } from "../api/contracts";
import type { SessionStore } from "./session-store";
import { State } from "../components/ui/States";
import { Button } from "../components/ui/controls";
import { Toast } from "../components/ui/Toast";
export type Services = { api: ApiClient; session: SessionStore };
const Context = createContext<Services | null>(null);
export function SessionProvider({
  services,
  children,
}: {
  services: Services;
  children: ReactNode;
}) {
  const snapshot = useSyncExternalStore(
    services.session.subscribe,
    services.session.getSnapshot,
    services.session.getSnapshot,
  );
  const [notice, setNotice] = useState<AuthNotice | null>(null);
  useEffect(() => {
    void services.session.load();
  }, [services]);
  useEffect(() => {
    if (snapshot.status === "ready" && snapshot.session.notice) {
      setNotice(snapshot.session.notice);
      services.session.consumeNotice();
    }
  }, [snapshot, services]);
  return (
    <Context value={services}>
      {children}
      {notice && (
        <Toast
          id={`auth-${notice.code}`}
          kind={notice.category === "error" ? "error" : "success"}
          message={notice.message}
          onDismiss={() => setNotice(null)}
        />
      )}
    </Context>
  );
}
export function useServices() {
  const value = useContext(Context);
  if (!value) throw Error("SessionProvider is required");
  return value;
}
export function useSession() {
  const { session } = useServices();
  return useSyncExternalStore(
    session.subscribe,
    session.getSnapshot,
    session.getSnapshot,
  );
}
export function AuthGuard({ children }: { children: ReactNode }) {
  const snapshot = useSession(),
    { session } = useServices(),
    signed = snapshot.status === "ready" && snapshot.session.signed_in;
  const mounted = useRef(false);
  if (signed) mounted.current = true;
  return (
    <>
      {!signed && (
        <div className="sh-container sh-foundation-page">
          {snapshot.status === "pending" ? (
            <State kind="loading" title="Connecting your session…" />
          ) : snapshot.status === "error" ? (
            <State
              heading="h1"
              kind="error"
              title="Your session could not be loaded"
              action={
                <Button
                  onClick={() => {
                    void session.refresh();
                  }}
                >
                  Try again
                </Button>
              }
            >
              Please try again.
            </State>
          ) : (
            <State heading="h1" kind="empty" title="Connect to Spotify">
              <p>Your session has ended. Connect to continue.</p>
              <form action="/login" method="get" data-navigation>
                <label className="sh-toggle">
                  <input
                    name="legal_consent"
                    value="1"
                    type="checkbox"
                    required
                  />
                  <span>I agree to the Terms and Privacy Policy</span>
                </label>
                <Button type="submit">Connect with Spotify</Button>
              </form>
            </State>
          )}
        </div>
      )}
      {(signed || mounted.current) && (
        <div hidden={!signed} inert={!signed}>
          {children}
        </div>
      )}
    </>
  );
}
