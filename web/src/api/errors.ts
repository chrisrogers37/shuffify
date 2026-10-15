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
