import type { ReactNode } from "react";
export function State({
  kind,
  title,
  children,
  action,
  heading: Heading = "h2",
}: {
  kind: "loading" | "empty" | "error" | "partial";
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  heading?: "h1" | "h2";
}) {
  return (
    <section
      className={`sh-state sh-foundation-state sh-state-${kind}`}
      aria-busy={kind === "loading" || undefined}
    >
      <Heading tabIndex={Heading === "h1" ? -1 : undefined}>{title}</Heading>
      {children && <div>{children}</div>}
      {action}
    </section>
  );
}
