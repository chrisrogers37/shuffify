import { useRef } from "react";
import { useTasks, Workspace } from "../components/tasks/TaskPanel";
import { Button } from "../components/ui/controls";
export function TaskScrollHarness() {
  const tasks = useTasks(),
    heading = useRef<HTMLHeadingElement>(null);
  return (
    <Workspace>
      <div className="sh-container sh-foundation-page">
        <h1 ref={heading} tabIndex={-1}>
          Task scroll restoration
        </h1>
        {["alice", "bob"].map((actor) => (
          <Button
            key={actor}
            onClick={(e) =>
              void tasks.openTask({
                id: `scroll-${actor}`,
                scrollKey: `${actor}:workshop:history`,
                title: `${actor} history`,
                trigger: e.currentTarget,
                fallback: heading.current,
                content: (
                  <div className="sh-foundation-stack">
                    {Array.from({ length: 70 }, (_, i) => (
                      <p key={i}>Saved view row {i + 1}</p>
                    ))}
                  </div>
                ),
              })
            }
          >
            Open {actor} history
          </Button>
        ))}
      </div>
    </Workspace>
  );
}
