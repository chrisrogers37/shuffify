import { Button, Field, Input, Select } from "../../../components/ui/controls";
import { useTasks } from "../../../components/tasks/TaskPanel";
import type { WorkshopToolContext } from "../workspace";
import { ToolFeedback, useRetry, useTools } from "./hooks";
export function AlgorithmTool({ controller: c }: WorkshopToolContext) {
  const { t, state, workshop } = useTools(c),
    tasks = useTasks(),
    s = state.algorithm,
    selected = workshop.context?.algorithms.find(
      (a) => a.class_name === s.selected,
    ),
    permission = c.permission("preview"),
    wait = useRetry(s.retryUntil);
  return (
    <div className="sh-tools-stack">
      <p>
        Try an arrangement in this draft. Spotify changes only after you review
        and save.
      </p>
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          void t.preview().then(async (applied) => {
            if (applied && tasks.narrow)
              for (let i = 0; i < tasks.stack.length; i++) {
                if (!(await tasks.closeTask())) break;
              }
          });
        }}
        className="sh-tools-stack"
      >
        <Field id="tools-algorithm" label="Shuffle algorithm">
          {(props) => (
            <Select
              {...props}
              value={s.selected}
              onChange={(e) => t.selectAlgorithm(e.currentTarget.value)}
            >
              {workshop.context?.algorithms.map((a) => (
                <option key={a.class_name} value={a.class_name}>
                  {a.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {selected && <p className="sh-tools-muted">{selected.description}</p>}
        {selected &&
          Object.entries(selected.parameters).map(([key, p]) => (
            <Field
              key={`${selected.class_name}-${key}`}
              id={`tools-param-${key}`}
              label={p.description}
              error={s.errors[key] ?? ""}
              help={
                p.type === "string"
                  ? ""
                  : [
                      p.min === undefined ? "" : `Minimum ${p.min}.`,
                      p.max === undefined ? "" : `Maximum ${p.max}.`,
                    ]
                      .filter(Boolean)
                      .join(" ")
              }
            >
              {(props) =>
                p.type === "string" ? (
                  <Select
                    {...props}
                    value={s.values[key] ?? ""}
                    onChange={(e) => t.setParameter(key, e.currentTarget.value)}
                  >
                    {p.options.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Input
                    {...props}
                    type="number"
                    min={p.min}
                    max={p.max}
                    step={p.type === "integer" ? 1 : "any"}
                    value={s.values[key] ?? ""}
                    onChange={(e) => t.setParameter(key, e.currentTarget.value)}
                  />
                )
              }
            </Field>
          ))}
        {!workshop.working.length && (
          <p>Add tracks to this draft before previewing a shuffle.</p>
        )}
        <p className="sh-tools-muted">
          Held tracks keep every matching occurrence in place. Changing draft
          tracks or locks while a preview loads requires a new preview.
        </p>
        <ToolFeedback value={s} id="tools-preview" />
        <Button
          type="submit"
          busy={s.busy}
          aria-disabled={
            !permission.allowed ||
            !selected ||
            !workshop.working.length ||
            wait > 0
          }
          aria-describedby={
            !permission.allowed ? "tools-preview-rights" : undefined
          }
        >
          {s.busy ? "Preparing preview…" : "Preview shuffle"}
        </Button>
        {!permission.allowed && (
          <p id="tools-preview-rights" className="sh-tools-muted">
            {permission.reason}
          </p>
        )}
      </form>
    </div>
  );
}
