import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { useConfirm } from "./ConfirmDialog";
import { createTaskScrollCache, createTransitionGate } from "./task-state";
import type { CloseReason } from "./task-state";
import { restoreFocus } from "../../a11y/focus";
import { Button } from "../ui/controls";
export type Task = {
  id: string;
  title: string;
  content: ReactNode;
  trigger: HTMLElement | null;
  fallback: HTMLElement | null;
  /** Include the authenticated actor in this opt-in restoration key. */
  scrollKey?: string;
  beforeClose?: (reason: CloseReason) => boolean | Promise<boolean>;
};
type Entry = Task & { scroll: number; pageScroll: number };
type Tasks = {
  openTask: (task: Task, mode?: "replace" | "push") => Promise<boolean>;
  closeTask: (reason?: CloseReason) => Promise<boolean>;
  active: boolean;
  narrow: boolean;
  stack: Entry[];
};
const Context = createContext<Tasks | null>(null);
function workspaceMediaQuery() {
  const breakpoint = getComputedStyle(document.documentElement)
    .getPropertyValue("--sh-breakpoint-workspace")
    .trim();
  if (!/^\d+(?:\.\d+)?px$/.test(breakpoint))
    throw Error("The shared workspace breakpoint token is missing.");
  return `(width < ${breakpoint})`;
}
export function TaskProvider({ children }: { children: ReactNode }) {
  const [stack, setStack] = useState<Entry[]>([]),
    entries = useRef<Entry[]>([]),
    gate = useRef(createTransitionGate()),
    scrollCache = useRef(createTaskScrollCache());
  const [narrow, setNarrow] = useState(
    () =>
      typeof window !== "undefined" &&
      matchMedia(workspaceMediaQuery()).matches,
  );
  const { open: modalOpen } = useConfirm();
  const primaryScroll = useRef(0);
  const update = (value: Entry[]) => {
    entries.current = value;
    setStack(value);
  };
  const openTask = useCallback(
    (task: Task, mode: "replace" | "push" = "replace") =>
      gate.current.run(async () => {
        const current = entries.current.at(-1);
        if (entries.current.some((entry) => entry.id === task.id)) return false;
        if (!current) primaryScroll.current = window.scrollY;
        if (
          current?.beforeClose &&
          !(await current.beforeClose(mode === "push" ? "suspend" : "replace"))
        )
          return false;
        if (current) {
          current.pageScroll = window.scrollY;
          scrollCache.current.save(current.scrollKey, current);
        }
        update([
          ...(mode === "push" ? entries.current : entries.current.slice(0, -1)),
          { ...task, ...scrollCache.current.read(task.scrollKey) },
        ]);
        return true;
      }),
    [],
  );
  const closeTask = useCallback(
    (reason: CloseReason = "close") =>
      gate.current.run(async () => {
        const current = entries.current.at(-1);
        if (!current) return false;
        if (current.beforeClose && !(await current.beforeClose(reason)))
          return false;
        scrollCache.current.save(current.scrollKey, current);
        update(entries.current.slice(0, -1));
        restoreFocus(current.trigger, current.fallback);
        if (!entries.current.length)
          requestAnimationFrame(() =>
            window.scrollTo({
              top: primaryScroll.current,
              behavior: "instant",
            }),
          );
        return true;
      }),
    [],
  );
  useEffect(() => {
    const media = matchMedia(workspaceMediaQuery()),
      change = () => setNarrow(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        !event.defaultPrevented &&
        entries.current.length &&
        !modalOpen &&
        !document.querySelector("dialog:modal")
      ) {
        event.preventDefault();
        void closeTask("escape");
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [closeTask, modalOpen]);
  const active = stack.at(-1);
  return (
    <Context value={{ openTask, closeTask, active: !!active, narrow, stack }}>
      {children}
      {/* The task layer is rendered by Workspace beside its primary content. */}
      <div hidden data-task-count={stack.length} />
    </Context>
  );
}
export function Workspace({ children }: { children: ReactNode }) {
  const tasks = useTasks();
  const { open: modalOpen } = useConfirm();
  const active = tasks.stack.at(-1);
  const activeHeading = useRef<HTMLHeadingElement>(null),
    body = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    activeHeading.current?.focus({ preventScroll: true });
    if (active && body.current) body.current.scrollTop = active.scroll;
    if (active && tasks.narrow)
      window.scrollTo({ top: active.pageScroll, behavior: "instant" });
  }, [active?.id, tasks.narrow]);
  useEffect(() => {
    if (!active || !tasks.narrow) return;
    const save = () => {
      active.pageScroll = window.scrollY;
    };
    window.addEventListener("scroll", save, { passive: true });
    return () => window.removeEventListener("scroll", save);
  }, [active, tasks.narrow]);
  return (
    <div
      className={`sh-workspace ${tasks.active ? "sh-workspace-panel-open" : ""} ${tasks.narrow ? "sh-panel-narrow" : ""}`}
    >
      <div
        data-panel-primary
        hidden={tasks.narrow && tasks.active}
        inert={tasks.narrow && tasks.active}
      >
        {children}
      </div>
      {tasks.stack.map((task, index) => {
        const selected = index === tasks.stack.length - 1;
        return (
          <section
            key={task.id}
            className="sh-task-panel"
            hidden={!selected}
            inert={!selected || modalOpen}
            aria-labelledby={`task-${task.id}-title`}
          >
            <div className="sh-panel-header">
              <h2
                id={`task-${task.id}-title`}
                tabIndex={-1}
                ref={selected ? activeHeading : undefined}
              >
                {task.title}
              </h2>
              <Button
                variant="secondary"
                onClick={() => {
                  void tasks.closeTask();
                }}
              >
                Close
              </Button>
            </div>
            <div
              className="sh-panel-body"
              ref={selected ? body : undefined}
              onScroll={(event) => {
                task.scroll = event.currentTarget.scrollTop;
              }}
            >
              {task.content}
            </div>
          </section>
        );
      })}
    </div>
  );
}
export function useTasks() {
  const value = useContext(Context);
  if (!value) throw Error("TaskProvider is required");
  return value;
}
