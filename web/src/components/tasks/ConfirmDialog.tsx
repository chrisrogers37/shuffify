import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { Button } from "../ui/controls";
import { restoreFocus } from "../../a11y/focus";
export type ConfirmOptions = {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
};
type Pending = ConfirmOptions & {
  resolve: (accepted: boolean) => void;
  trigger: HTMLElement | null;
};
const Context = createContext<{
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  open: boolean;
}>({ confirm: async () => false, open: false });
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const pendingRef = useRef<Pending | null>(null),
    dialog = useRef<HTMLDialogElement>(null),
    cancel = useRef<HTMLButtonElement>(null);
  const pointerOnBackdrop = useRef(false);
  const confirm = useCallback((options: ConfirmOptions) => {
    if (pendingRef.current) return Promise.resolve(false);
    return new Promise<boolean>((resolve) => {
      const value = {
        ...options,
        resolve,
        trigger:
          document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null,
      };
      pendingRef.current = value;
      setPending(value);
    });
  }, []);
  const finish = useCallback((accepted: boolean) => {
    const current = pendingRef.current;
    if (!current) return;
    dialog.current?.close();
    pendingRef.current = null;
    setPending(null);
    current.resolve(accepted);
    restoreFocus(
      current.trigger,
      document.querySelector<HTMLElement>("#main-content h1"),
    );
  }, []);
  useLayoutEffect(() => {
    if (pending && dialog.current && !dialog.current.open) {
      dialog.current.showModal();
      cancel.current?.focus();
    }
  }, [pending]);
  return (
    <Context value={{ confirm, open: !!pending }}>
      {children}
      <dialog
        ref={dialog}
        className="sh-task-panel sh-panel-modal sh-confirm-dialog"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-message"
        onKeyDown={(event) => {
          if (event.key !== "Tab") return;
          const controls = Array.from(
            event.currentTarget.querySelectorAll<HTMLElement>(
              'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]',
            ),
          ).filter((control) => control.getClientRects().length > 0);
          const first = controls[0],
            last = controls.at(-1);
          if (!first || !last) {
            event.preventDefault();
            return;
          }
          if (
            event.shiftKey &&
            (document.activeElement === first ||
              !event.currentTarget.contains(document.activeElement))
          ) {
            event.preventDefault();
            last.focus();
          } else if (
            !event.shiftKey &&
            (document.activeElement === last ||
              !event.currentTarget.contains(document.activeElement))
          ) {
            event.preventDefault();
            first.focus();
          }
        }}
        onCancel={(event) => {
          event.preventDefault();
          finish(false);
        }}
        onPointerDown={(event) => {
          pointerOnBackdrop.current = event.target === event.currentTarget;
        }}
        onClick={(event) => {
          if (pointerOnBackdrop.current && event.target === event.currentTarget)
            finish(false);
          pointerOnBackdrop.current = false;
        }}
      >
        <div className="sh-panel-header">
          <h2 id="confirm-title">{pending?.title}</h2>
        </div>
        <div className="sh-panel-body">
          <p id="confirm-message">{pending?.message}</p>
        </div>
        <div className="sh-panel-footer">
          <button
            className="sh-button-secondary"
            ref={cancel}
            onClick={() => finish(false)}
          >
            {pending?.cancelLabel ?? "Cancel"}
          </button>
          <Button
            variant={pending?.danger ? "danger" : "primary"}
            onClick={() => finish(true)}
          >
            {pending?.confirmLabel ?? "Continue"}
          </Button>
        </div>
      </dialog>
    </Context>
  );
}
export const useConfirm = () => useContext(Context);
