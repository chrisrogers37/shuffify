import type { ComponentProps, ReactNode } from "react";
type ButtonProps = Omit<ComponentProps<"button">, "style"> & {
  variant?: "primary" | "secondary" | "danger";
  busy?: boolean;
};
export function Button({
  variant = "primary",
  busy = false,
  children,
  onClick,
  ...props
}: ButtonProps) {
  const className =
    variant === "primary"
      ? "sh-button"
      : variant === "danger"
        ? "sh-button-danger"
        : "sh-button-secondary";
  return (
    <button
      {...props}
      type={props.type ?? "button"}
      className={`${className} ${props.className ?? ""}`}
      aria-busy={busy || undefined}
      aria-disabled={busy || props["aria-disabled"]}
      onClick={(event) => {
        if (
          busy ||
          props["aria-disabled"] === true ||
          props["aria-disabled"] === "true"
        ) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
      }}
    >
      {children}
    </button>
  );
}
export function Input(props: Omit<ComponentProps<"input">, "style">) {
  return <input {...props} className={`sh-input ${props.className ?? ""}`} />;
}
export function Select(props: Omit<ComponentProps<"select">, "style">) {
  return <select {...props} className={`sh-input ${props.className ?? ""}`} />;
}
export function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="sh-toggle">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.currentTarget.checked)}
      />
      <span>{label}</span>
    </label>
  );
}
export function Field({
  id,
  label,
  help,
  error,
  children,
}: {
  id: string;
  label: string;
  help?: string;
  error?: string;
  children: (props: {
    id: string;
    "aria-describedby"?: string;
    "aria-invalid"?: true;
  }) => ReactNode;
}) {
  const described = [help ? `${id}-help` : null, error ? `${id}-error` : null]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="sh-field">
      <label className="sh-field-label" htmlFor={id}>
        {label}
      </label>
      {children({
        id,
        ...(described ? { "aria-describedby": described } : {}),
        ...(error ? { "aria-invalid": true } : {}),
      })}
      {help && (
        <p id={`${id}-help`} className="sh-field-help">
          {help}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="sh-field-error">
          {error}
        </p>
      )}
    </div>
  );
}
