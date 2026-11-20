import type { ApiError } from "./contracts.ts";

export class ProtocolError extends Error {
  readonly status: number | null;
  constructor(
    message: string,
    status: number | null = null,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "ProtocolError";
    this.status = status;
  }
}

export class ApiRequestError extends Error {
  readonly status: number;
  readonly error: ApiError;
  /** Full server retry hint, in milliseconds; null means no hint was supplied. */
  readonly retryAfterMs: number | null;
  constructor(
    status: number,
    error: ApiError,
    retryAfterMs: number | null = null,
  ) {
    super(error.message);
    this.name = "ApiRequestError";
    this.status = status;
    this.error = error;
    this.retryAfterMs = retryAfterMs;
  }
}
export class UnauthorizedError extends ApiRequestError {
  constructor(error: ApiError, retryAfterMs: number | null = null) {
    super(401, error, retryAfterMs);
    this.name = "UnauthorizedError";
  }
}
export class ConflictError extends ApiRequestError {
  constructor(error: ApiError, retryAfterMs: number | null = null) {
    super(409, error, retryAfterMs);
    this.name = "ConflictError";
  }
}
export class RateLimitError extends ApiRequestError {
  constructor(error: ApiError, retryAfterMs: number | null = null) {
    super(429, error, retryAfterMs);
    this.name = "RateLimitError";
  }
}
/** The wait after a 429 that has no retry hint. */
const rateLimitFallbackMs = 1000;
/**
 * When a failed request may be sent again, or null if the failure sets no
 * deadline: a 429 waits its retry hint, or one second without one, and a 503
 * waits its retry hint if it has one. HTTP gives Retry-After a meaning on
 * these two statuses only, so a hint on any other failure is ignored.
 */
export function retryDeadline(error: unknown, now: number): number | null {
  if (!(error instanceof ApiRequestError)) return null;
  if (error.status === 429)
    return now + (error.retryAfterMs ?? rateLimitFallbackMs);
  if (error.status === 503 && error.retryAfterMs !== null)
    return now + error.retryAfterMs;
  return null;
}
export class NetworkError extends Error {
  readonly admissionUnknown: boolean;
  constructor(method: string, cause: unknown) {
    const unsafe = !["GET", "HEAD", "OPTIONS"].includes(method);
    super(
      unsafe
        ? "The request could not be confirmed. Its operation may have been accepted."
        : "The server could not be reached.",
      { cause },
    );
    this.name = "NetworkError";
    this.admissionUnknown = unsafe;
  }
}
export class MissingCsrfTokenError extends Error {
  constructor() {
    super("Your session must be loaded before this change can be submitted.");
    this.name = "MissingCsrfTokenError";
  }
}
