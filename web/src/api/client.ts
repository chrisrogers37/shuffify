import type {
  ApiError,
  Operation,
  OperationContext,
  SessionContext,
} from "./contracts.ts";
import {
  ApiRequestError,
  ConflictError,
  MissingCsrfTokenError,
  NetworkError,
  ProtocolError,
  RateLimitError,
  UnauthorizedError,
} from "./errors.ts";
import { fetchTransport } from "./transport.ts";
import type { Transport } from "./transport.ts";
export type { Transport } from "./transport.ts";

export interface ApiClientOptions {
  transport?: Transport;
  getCsrfToken?: () => string | null | undefined;
  onUnauthorized?: () => void;
}
export interface ApiRequestInit extends Omit<
  RequestInit,
  "credentials" | "redirect" | "mode"
> {
  /** JSON serialization is explicit; FormData and other native bodies are left intact. */
  json?: unknown;
}
export type ApiResult<T> =
  | { status: "accepted"; operation: Operation }
  | { status: "completed"; data: T };

type RecordValue = Record<string, unknown>;
const states = new Set([
  "queued",
  "running",
  "compensating",
  "succeeded",
  "failed",
  "rolled_back",
  "needs_attention",
]);
const noticeCodes = new Set([
  "access_denied",
  "oauth_failed",
  "missing_code",
  "invalid_state",
  "authentication_failed",
  "consent_required",
  "login_failed",
]);
const isRecord = (value: unknown): value is RecordValue =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isId = (value: unknown): value is number =>
  Number.isSafeInteger(value) && (value as number) > 0;
const isCount = (value: unknown): value is number =>
  Number.isSafeInteger(value) && (value as number) >= 0;
const isNullableString = (value: unknown): value is string | null =>
  value === null || typeof value === "string";

function assertApiPath(path: string): void {
  const pathname = path.split("?")[0] ?? "";
  if (
    !path.startsWith("/api/") ||
    /[\\#\u0000-\u0020\u007f]/u.test(path) ||
    /%(?:2e|2f|5c)/iu.test(pathname)
  ) {
    throw new TypeError("API requests require a root-relative /api/ path.");
  }
  const url = new URL(path, "https://shuffify.invalid");
  if (url.origin !== "https://shuffify.invalid" || url.pathname !== pathname) {
    throw new TypeError(
      "API paths cannot escape or normalize to another endpoint.",
    );
  }
}

/** Validate fields needed to observe an operation; deep page DTOs use shared schema tests. */
export function isOperation(value: unknown): value is Operation {
  if (
    !isRecord(value) ||
    !isId(value.id) ||
    typeof value.kind !== "string" ||
    value.kind.length === 0 ||
    typeof value.state !== "string" ||
    !states.has(value.state) ||
    !Array.isArray(value.targets) ||
    !value.targets.every((target) => typeof target === "string") ||
    !isNullableString(value.started_at) ||
    !isNullableString(value.completed_at) ||
    !isRecord(value.progress) ||
    !(value.result === null || isRecord(value.result)) ||
    !isRecord(value.recovery)
  )
    return false;
  const progress = value.progress;
  const recovery = value.recovery;
  return (
    (progress.batches_done === undefined || isCount(progress.batches_done)) &&
    (progress.total_batches === undefined || isCount(progress.total_batches)) &&
    (progress.phase === undefined ||
      progress.phase === "forward" ||
      progress.phase === "compensating") &&
    typeof recovery.available === "boolean" &&
    (recovery.action === null || recovery.action === "restore") &&
    isNullableString(recovery.reason)
  );
}

function isApiError(value: RecordValue): value is RecordValue & ApiError {
  return (
    value.success === false &&
    typeof value.error_code === "string" &&
    typeof value.message === "string" &&
    typeof value.category === "string" &&
    (value.retryable === undefined || typeof value.retryable === "boolean") &&
    (value.retry_after === undefined ||
      (typeof value.retry_after === "number" &&
        Number.isFinite(value.retry_after) &&
        value.retry_after >= 0)) &&
    (value.operation_id === undefined || isId(value.operation_id)) &&
    (value.field_errors === undefined ||
      (Array.isArray(value.field_errors) &&
        value.field_errors.every(
          (field) =>
            isRecord(field) &&
            typeof field.path === "string" &&
            typeof field.message === "string",
        )))
  );
}

function retryHint(response: Response, body: ApiError): number | null {
  const header = response.headers.get("Retry-After");
  let milliseconds: number | null = null;
  if (header !== null) {
    const numeric = /^\s*\d+(?:\.\d+)?\s*$/u.test(header)
      ? Number(header) * 1000
      : null;
    const date = numeric === null ? Date.parse(header) : Number.NaN;
    const hint =
      numeric ??
      (Number.isFinite(date) ? Math.max(0, date - Date.now()) : null);
    if (hint !== null && Number.isFinite(hint)) milliseconds = hint;
  }
  if (body.retry_after !== undefined)
    milliseconds = Math.max(milliseconds ?? 0, body.retry_after * 1000);
  return milliseconds;
}

function assertSession(value: unknown): asserts value is SessionContext {
  if (
    !isRecord(value) ||
    value.success !== true ||
    typeof value.signed_in !== "boolean" ||
    !(
      value.notice === null ||
      (isRecord(value.notice) &&
        typeof value.notice.code === "string" &&
        noticeCodes.has(value.notice.code) &&
        ["error", "success", "info"].includes(String(value.notice.category)) &&
        typeof value.notice.message === "string")
    )
  ) {
    throw new ProtocolError("The session response is incomplete.", 200);
  }
  if (value.signed_in) {
    if (
      !isRecord(value.user) ||
      typeof value.user.id !== "string" ||
      value.user.id.length === 0 ||
      !isNullableString(value.user.display_name) ||
      !isNullableString(value.user.image_url) ||
      typeof value.csrf_token !== "string" ||
      value.csrf_token.length === 0
    ) {
      throw new ProtocolError(
        "The signed-in session response is incomplete.",
        200,
      );
    }
  } else if (value.user !== null || value.csrf_token !== undefined) {
    throw new ProtocolError(
      "The signed-out session response is inconsistent.",
      200,
    );
  }
}

export function createApiClient(options: ApiClientOptions = {}) {
  const transport = options.transport ?? fetchTransport;
  async function request<T>(
    path: string,
    init: ApiRequestInit = {},
  ): Promise<ApiResult<T>> {
    assertApiPath(path);
    const { json, ...fetchInit } = init;
    const method = (init.method ?? "GET").toUpperCase();
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    let body = init.body;
    if ("json" in init) {
      if (body !== undefined && body !== null)
        throw new TypeError("Use either json or body, not both.");
      body = JSON.stringify(json);
      if (body === undefined)
        throw new TypeError("The json payload must be serializable.");
      headers.set("Content-Type", "application/json");
    }
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const token = options.getCsrfToken?.();
      if (!token) throw new MissingCsrfTokenError();
      headers.set("X-CSRFToken", token);
    }
    let response: Response;
    try {
      response = await transport(path, {
        ...fetchInit,
        ...(body === undefined ? {} : { body }),
        method,
        headers,
        credentials: "same-origin",
        mode: "same-origin",
        redirect: "error",
      });
    } catch (error) {
      if (init.signal?.aborted && ["GET", "HEAD", "OPTIONS"].includes(method))
        throw (
          init.signal.reason ??
          new DOMException("Observation aborted.", "AbortError")
        );
      throw new NetworkError(method, error);
    }
    if (response.status === 401) options.onUnauthorized?.();
    let data: unknown;
    try {
      data = await response.json();
    } catch (error) {
      if (init.signal?.aborted && ["GET", "HEAD", "OPTIONS"].includes(method))
        throw (
          init.signal.reason ??
          new DOMException("Observation aborted.", "AbortError")
        );
      if (!(error instanceof SyntaxError))
        throw new NetworkError(method, error);
      throw new ProtocolError(
        "The server returned an empty or invalid JSON response.",
        response.status,
        { cause: error },
      );
    }
    if (!isRecord(data) || typeof data.success !== "boolean")
      throw new ProtocolError(
        "The response envelope is missing.",
        response.status,
      );
    if (!response.ok) {
      if (!isApiError(data))
        throw new ProtocolError(
          "The HTTP error response is inconsistent.",
          response.status,
        );
      const hint = retryHint(response, data);
      if (response.status === 401) {
        throw new UnauthorizedError(data, hint);
      }
      if (response.status === 409) throw new ConflictError(data, hint);
      if (response.status === 429) throw new RateLimitError(data, hint);
      throw new ApiRequestError(response.status, data, hint);
    }
    if (data.success !== true)
      throw new ProtocolError(
        "A successful HTTP response must contain a successful envelope.",
        response.status,
      );
    if (response.status === 202) {
      if (
        !isId(data.operation_id) ||
        !isOperation(data.operation) ||
        data.operation.id !== data.operation_id
      ) {
        throw new ProtocolError(
          "Accepted writes must identify their operation.",
          response.status,
        );
      }
      return { status: "accepted", operation: data.operation };
    }
    // Admission envelopes must never become completed writes merely because their status changed.
    if ("operation_id" in data)
      throw new ProtocolError(
        "An operation admission requires HTTP 202.",
        response.status,
      );
    const match = /^\/api\/operations\/(\d+)(?:\?|$)/u.exec(path);
    if (
      match &&
      (!isOperation(data.operation) || data.operation.id !== Number(match[1]))
    ) {
      throw new ProtocolError(
        "The operation response does not match the requested operation.",
        response.status,
      );
    }
    return { status: "completed", data: data as T };
  }
  async function getSession(
    init: Pick<ApiRequestInit, "signal"> = {},
  ): Promise<SessionContext> {
    const result = await request<SessionContext>("/api/session", init);
    if (result.status !== "completed")
      throw new ProtocolError("Session reads cannot accept operations.", 202);
    assertSession(result.data);
    return result.data;
  }
  async function getOperation(
    id: number,
    init: Pick<ApiRequestInit, "signal"> = {},
  ): Promise<Operation> {
    if (!isId(id))
      throw new TypeError("Operation IDs must be positive integers.");
    const result = await request<OperationContext>(
      `/api/operations/${id}`,
      init,
    );
    if (result.status !== "completed")
      throw new ProtocolError("Operation reads cannot accept operations.", 202);
    return result.data.operation;
  }
  return { request, getSession, getOperation };
}
export type ApiClient = ReturnType<typeof createApiClient>;
