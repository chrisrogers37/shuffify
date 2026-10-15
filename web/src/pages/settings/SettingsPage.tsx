import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { SettingsContext } from "../../api/contracts";
import { ApiRequestError, RateLimitError } from "../../api/errors";
import { useServices, useSession } from "../../auth/SessionProvider";
import { Button, Field, Input, Select } from "../../components/ui/controls";
import { State } from "../../components/ui/States";
import { Toast } from "../../components/ui/Toast";
import { useNavigationBlocker } from "../../navigation/NavigationGuard";
import {
  draftFromSettings,
  isDirty,
  loadSettings,
  saveSettings,
  settingsProblem,
  validateDraft,
} from "./model";
import type { FieldErrors, SettingsDraft, SettingsProblem } from "./model";
const labels: Record<keyof SettingsDraft, string> = {
  default_algorithm: "Default shuffle algorithm",
  auto_snapshot_enabled: "Save a snapshot before shuffling",
  max_snapshots_per_playlist: "Maximum snapshots per playlist",
  dashboard_show_recent_activity: "Show recent activity",
};
function useRetrySeconds(until: number): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (until <= Date.now()) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= until) clearInterval(timer);
    }, 250);
    return () => clearInterval(timer);
  }, [until]);
  return Math.max(0, Math.ceil((until - now) / 1000));
}
function SettingsToggle({
  name,
  checked,
  help,
  error,
  onChange,
}: {
  name: "auto_snapshot_enabled" | "dashboard_show_recent_activity";
  checked: boolean;
  help: string;
  error?: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="sh-field">
      <label className="sh-toggle" htmlFor={name}>
        <span className="sh-field-label">{labels[name]}</span>
        <input
          type="checkbox"
          id={name}
          name={name}
          checked={checked}
          aria-describedby={`${name}-help${error ? ` ${name}-error` : ""}`}
          aria-invalid={error ? true : undefined}
          onChange={(event) => onChange(event.currentTarget.checked)}
        />
      </label>
      <p className="sh-field-help" id={`${name}-help`}>
        {help}
      </p>
      {error && (
        <p className="sh-field-error" id={`${name}-error`}>
          {error}
        </p>
      )}
    </div>
  );
}
export function SettingsPage() {
  const { api } = useServices(),
    session = useSession(),
    id = useId();
  const [context, setContext] = useState<SettingsContext | null>(null),
    [draft, setDraft] = useState<SettingsDraft | null>(null),
    [loadError, setLoadError] = useState(""),
    [reload, setReload] = useState(0),
    [loading, setLoading] = useState(true),
    [saving, setSaving] = useState(false),
    [fields, setFields] = useState<FieldErrors>({}),
    [problem, setProblem] = useState<SettingsProblem | null>(null),
    [retryUntil, setRetryUntil] = useState(0),
    [toast, setToast] = useState<{ id: string; message: string } | null>(null);
  const inFlight = useRef(false),
    mounted = useRef(false),
    read = useRef<{
      attempt: number;
      promise: Promise<SettingsContext>;
    } | null>(null),
    errorRef = useRef<HTMLDivElement>(null),
    limitRef = useRef<HTMLInputElement>(null),
    saveSequence = useRef(0);
  const seconds = useRetrySeconds(retryUntil),
    dirty = !!context && !!draft && isDirty(draft, context.settings);
  const csrf =
    session.status === "ready" && session.session.signed_in
      ? session.session.csrf_token
      : "";
  const identity =
    session.status === "ready" && session.session.signed_in
      ? session.session.user.display_name || session.session.user.id
      : "";
  useNavigationBlocker(
    `${id}-settings`,
    dirty || saving,
    saving
      ? "A settings save is still waiting for a response. Leaving will not cancel it. Unsaved changes on this page will be lost."
      : "Your unsaved settings will be lost.",
  );
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError("");
    if (read.current?.attempt !== reload)
      read.current = { attempt: reload, promise: loadSettings(api) };
    void read.current.promise.then(
      (value) => {
        if (active) {
          setContext(value);
          setDraft(draftFromSettings(value.settings));
          setLoading(false);
        }
      },
      (error) => {
        if (!active) return;
        setLoadError(
          error instanceof ApiRequestError
            ? error.message
            : "The settings response could not be loaded. Please try again.",
        );
        if (error instanceof RateLimitError)
          setRetryUntil(Date.now() + (error.retryAfterMs ?? 1000));
        setLoading(false);
      },
    );
    return () => {
      active = false;
    };
  }, [api, reload]);
  useEffect(() => {
    if (problem) errorRef.current?.focus();
  }, [problem]);
  function change<K extends keyof SettingsDraft>(
    key: K,
    value: SettingsDraft[K],
  ) {
    setDraft((previous) =>
      previous ? { ...previous, [key]: value } : previous,
    );
    setFields((previous) => {
      const next = { ...previous };
      delete next[key];
      return next;
    });
    setToast(null);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || seconds > 0 || !draft) return;
    const invalid = validateDraft(draft);
    setFields(invalid);
    setProblem(null);
    setToast(null);
    if (Object.keys(invalid).length) {
      limitRef.current?.focus();
      return;
    }
    // Capture before disabling the controls; the shared client never replays a POST.
    const captured = { ...draft };
    inFlight.current = true;
    setSaving(true);
    try {
      const saved = await saveSettings(api, captured);
      if (mounted.current) {
        setContext(saved);
        setDraft(draftFromSettings(saved.settings));
        setFields({});
        setRetryUntil(0);
        setToast({
          id: `${id}-saved-${++saveSequence.current}`,
          message: saved.message || "Settings saved successfully.",
        });
      }
    } catch (error) {
      if (mounted.current) {
        const value = settingsProblem(error);
        setProblem(value);
        setFields(value.fields);
        if (value.retryAfterMs !== null)
          setRetryUntil(Date.now() + value.retryAfterMs);
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setSaving(false);
    }
  }
  const unavailableDefault =
    !!draft?.default_algorithm &&
    !context?.algorithm_options.some(
      (option) => option.value === draft.default_algorithm,
    );
  return (
    <div className="sh-container sh-settings-page">
      <div className="sh-settings-content">
        <header className="sh-settings-heading">
          <h1 className="sh-heading" tabIndex={-1}>
            Settings
          </h1>
          <p className="sh-text-muted">
            Your preferences for shuffling, saved history, and your library.
          </p>
        </header>
        {loading ? (
          <State kind="loading" title="Loading settings…" />
        ) : loadError ? (
          <State
            kind="error"
            title="Settings could not be loaded"
            action={
              <Button
                aria-disabled={seconds > 0}
                onClick={() => setReload((value) => value + 1)}
              >
                Try again
              </Button>
            }
          >
            <p>{loadError}</p>
            {seconds > 0 && (
              <p aria-live="off">
                Try again in {seconds} {seconds === 1 ? "second" : "seconds"}.
              </p>
            )}
          </State>
        ) : (
          context &&
          draft && (
            <>
              <form
                id="settings-form"
                onSubmit={(event) => {
                  void submit(event);
                }}
                noValidate
                aria-busy={saving || undefined}
              >
                <fieldset
                  id="settings-fields"
                  className="sh-settings-fields"
                  disabled={saving}
                >
                  <legend className="sr-only">App preferences</legend>
                  <section
                    className="sh-settings-section"
                    aria-labelledby="shuffle-settings-title"
                  >
                    <h2 className="sh-heading" id="shuffle-settings-title">
                      Shuffle defaults
                    </h2>
                    <Field
                      id="default_algorithm"
                      label={labels.default_algorithm}
                      help="Pre-select this algorithm when shuffling playlists."
                      {...(fields.default_algorithm
                        ? { error: fields.default_algorithm }
                        : {})}
                    >
                      {(props) => (
                        <Select
                          {...props}
                          name="default_algorithm"
                          value={draft.default_algorithm}
                          onChange={(event) =>
                            change(
                              "default_algorithm",
                              event.currentTarget.value,
                            )
                          }
                        >
                          {unavailableDefault && (
                            <option value={draft.default_algorithm}>
                              Saved algorithm (unavailable)
                            </option>
                          )}
                          {context.algorithm_options.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </Select>
                      )}
                    </Field>
                    {unavailableDefault && (
                      <p className="sh-field-help">
                        Your saved algorithm is currently unavailable. Choose an
                        available algorithm before saving.
                      </p>
                    )}
                  </section>
                  <section
                    className="sh-settings-section"
                    aria-labelledby="history-settings-title"
                  >
                    <h2 className="sh-heading" id="history-settings-title">
                      Snapshots &amp; History
                    </h2>
                    <SettingsToggle
                      name="auto_snapshot_enabled"
                      checked={draft.auto_snapshot_enabled}
                      help="Keep the playlist order so you can restore it later."
                      {...(fields.auto_snapshot_enabled
                        ? { error: fields.auto_snapshot_enabled }
                        : {})}
                      onChange={(value) =>
                        change("auto_snapshot_enabled", value)
                      }
                    />
                    <Field
                      id="max_snapshots_per_playlist"
                      label={labels.max_snapshots_per_playlist}
                      help="Keep 1–50 snapshots. The oldest is removed when the limit is reached."
                      {...(fields.max_snapshots_per_playlist
                        ? { error: fields.max_snapshots_per_playlist }
                        : {})}
                    >
                      {(props) => (
                        <Input
                          {...props}
                          ref={limitRef}
                          type="number"
                          name="max_snapshots_per_playlist"
                          min="1"
                          max="50"
                          step="1"
                          required
                          className="sh-settings-limit"
                          value={draft.max_snapshots_per_playlist}
                          onChange={(event) =>
                            change(
                              "max_snapshots_per_playlist",
                              event.currentTarget.value,
                            )
                          }
                        />
                      )}
                    </Field>
                  </section>
                  <section
                    className="sh-settings-section"
                    aria-labelledby="library-settings-title"
                  >
                    <h2 className="sh-heading" id="library-settings-title">
                      Library
                    </h2>
                    <SettingsToggle
                      name="dashboard_show_recent_activity"
                      checked={draft.dashboard_show_recent_activity}
                      help="Show recent shuffle and raid activity in your library."
                      {...(fields.dashboard_show_recent_activity
                        ? { error: fields.dashboard_show_recent_activity }
                        : {})}
                      onChange={(value) =>
                        change("dashboard_show_recent_activity", value)
                      }
                    />
                  </section>
                </fieldset>
                {problem && (
                  <div
                    id="settings-error"
                    className="sh-form-error"
                    role="alert"
                    tabIndex={-1}
                    ref={errorRef}
                  >
                    <p>{problem.message}</p>
                    {Object.keys(fields).length > 0 && (
                      <ul>
                        {(Object.keys(fields) as (keyof SettingsDraft)[]).map(
                          (key) => (
                            <li key={key}>
                              <a
                                href={`#${key}`}
                                onClick={(event) => {
                                  event.preventDefault();
                                  document.getElementById(key)?.focus();
                                }}
                              >
                                {labels[key]}
                              </a>
                            </li>
                          ),
                        )}
                      </ul>
                    )}
                  </div>
                )}
                {seconds > 0 && (
                  <p
                    className="sh-field-help sh-settings-retry"
                    aria-live="off"
                  >
                    Try again in {seconds}{" "}
                    {seconds === 1 ? "second" : "seconds"}.
                  </p>
                )}
                <div className="sh-settings-save">
                  <p className="sh-field-help">Saves your app preferences.</p>
                  <Button
                    type="submit"
                    busy={saving}
                    aria-disabled={seconds > 0 || unavailableDefault}
                  >
                    {saving ? "Saving…" : "Save settings"}
                  </Button>
                </div>
              </form>
              <section
                className="sh-settings-section sh-settings-account"
                aria-labelledby="account-settings-title"
              >
                <h2 className="sh-heading" id="account-settings-title">
                  Account
                </h2>
                <p className="sh-text-muted">
                  Connected as{" "}
                  <strong className="sh-settings-account-name">
                    {identity}
                  </strong>
                  .
                </p>
                <dl className="sh-settings-unavailable">
                  <div>
                    <dt className="sh-field-label">Appearance</dt>
                    <dd className="sh-field-help">
                      Theme selection is not available yet. Your saved
                      preference is preserved.
                    </dd>
                  </div>
                  <div>
                    <dt className="sh-field-label">Routine notifications</dt>
                    <dd className="sh-field-help">
                      Notifications are not available yet. Your saved preference
                      is preserved.
                    </dd>
                  </div>
                </dl>
                <form
                  id="settings-logout-form"
                  action="/logout"
                  method="post"
                  data-navigation
                  data-app-logout
                >
                  <input type="hidden" name="csrf_token" value={csrf} />
                  <Button type="submit" variant="secondary" disabled={saving}>
                    Log out
                  </Button>
                </form>
              </section>
            </>
          )
        )}
        {toast && (
          <Toast
            id={toast.id}
            message={toast.message}
            onDismiss={() => setToast(null)}
          />
        )}
      </div>
    </div>
  );
}
