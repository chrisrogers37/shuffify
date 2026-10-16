import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
type Announce = (
  message: string,
  id: string,
  priority?: "polite" | "assertive",
) => void;
const Context = createContext<Announce>(() => {});
export function Announcer({ children }: { children: ReactNode }) {
  const seen = useRef(new Set<string>());
  const [messages, setMessages] = useState({
    polite: { id: "", message: "" },
    assertive: { id: "", message: "" },
  });
  const announce = useCallback<Announce>((message, id, priority = "polite") => {
    if (seen.current.has(id)) return;
    seen.current.add(id);
    if (seen.current.size > 100)
      seen.current.delete(seen.current.values().next().value!);
    setMessages((previous) => ({ ...previous, [priority]: { id, message } }));
  }, []);
  return (
    <Context value={announce}>
      {children}
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        <span key={messages.polite.id}>{messages.polite.message}</span>
      </div>
      <div className="sr-only" aria-live="assertive" aria-atomic="true">
        <span key={messages.assertive.id}>{messages.assertive.message}</span>
      </div>
    </Context>
  );
}
export const useAnnouncer = () => useContext(Context);
