import { useEffect } from "react";
import { useAnnouncer } from "../../a11y/Announcer";
import { Button } from "./controls";
export function Toast({
  id,
  message,
  kind = "success",
  onDismiss,
}: {
  id: string;
  message: string;
  kind?: "success" | "error";
  onDismiss: () => void;
}) {
  const announce = useAnnouncer();
  useEffect(
    () => announce(message, id, kind === "error" ? "assertive" : "polite"),
    [announce, id, message, kind],
  );
  return (
    <div className={`sh-toast sh-toast-${kind}`}>
      <p>{message}</p>
      <Button variant="secondary" onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  );
}
