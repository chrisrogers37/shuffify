import { useLayoutEffect, useRef, useState } from "react";
import { Button } from "../../components/ui/controls";
import { restoreFocus } from "../../a11y/focus";
export function useDraftDecision() {
  const [pending, setPending] = useState<{
      action: string;
      attention: boolean;
      resolve: (choice: "review" | "discard" | "stay") => void;
    } | null>(null),
    current = useRef<typeof pending>(null),
    dialog = useRef<HTMLDialogElement>(null),
    stay = useRef<HTMLButtonElement>(null),
    trigger = useRef<HTMLElement | null>(null);
  function choose(choice: "review" | "discard" | "stay") {
    const p = current.current;
    if (!p) return;
    dialog.current?.close();
    current.current = null;
    setPending(null);
    p.resolve(choice);
    restoreFocus(trigger.current, document.querySelector("#main-content h1"));
  }
  useLayoutEffect(() => {
    if (pending) {
      dialog.current?.showModal();
      stay.current?.focus();
    }
  }, [pending]);
  return {
    cancel: () => choose("stay"),
    ask: (action: string, attention = false) => {
      if (current.current) return Promise.resolve("stay" as const);
      return new Promise<"review" | "discard" | "stay">((resolve) => {
        trigger.current =
          document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
        const p = { action, attention, resolve };
        current.current = p;
        setPending(p);
      });
    },
    element: (
      <dialog
        ref={dialog}
        className="sh-task-panel sh-panel-modal sh-confirm-dialog"
        aria-labelledby="workshop-leave-title"
        aria-describedby="workshop-leave-message"
        onCancel={(e) => {
          e.preventDefault();
          choose("stay");
        }}
        onKeyDown={(e) => {
          if (e.key !== "Tab") return;
          const controls = Array.from(
            e.currentTarget.querySelectorAll("button"),
          );
          const first = controls[0],
            last = controls.at(-1);
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }}
      >
        <div className="sh-panel-header">
          <h2 id="workshop-leave-title">
            {pending?.attention
              ? "Keep the operation details?"
              : "Keep your local draft?"}
          </h2>
        </div>
        <div className="sh-panel-body">
          <p id="workshop-leave-message">
            {pending?.attention
              ? "A Spotify operation or its result still needs review. Leaving does not cancel it. The exact request and local details are retained only in this document."
              : `Your draft has unsaved changes. Review and save, discard the local changes, or stay before you ${pending?.action ?? "continue"}.`}
          </p>
        </div>
        <div className="sh-panel-actions">
          <Button ref={stay} variant="secondary" onClick={() => choose("stay")}>
            Stay
          </Button>
          <Button variant="secondary" onClick={() => choose("review")}>
            {pending?.attention ? "View results" : "Review & save"}
          </Button>
          <Button variant="danger" onClick={() => choose("discard")}>
            {pending?.attention ? "Leave page" : "Discard"}
          </Button>
        </div>
      </dialog>
    ),
  };
}
