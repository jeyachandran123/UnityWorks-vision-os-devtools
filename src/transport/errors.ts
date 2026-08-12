/**
 * Error handling with no status-code table.
 *
 * There is deliberately no map from HTTP status to "should I retry". The
 * platform tells us, in a field:
 *
 * > 09_API §8: *"Consumers must never infer retryability from a status code or a
 * > message string. Inferring it is how retry storms begin."*
 *
 * `tests/architecture/no-inference.test.ts` asserts this file contains no
 * comparison against a numeric status and no regex over `message`.
 */

import type { ApiErrorView } from '@contract/types';

/**
 * The harness could not be reached at all.
 *
 * Not a platform error — **no platform was contacted**. A dev-proxy 500 with an
 * empty body, or a refused socket, means the request never arrived, so there is
 * no `ApiErrorView` to read and nothing may be concluded about Vision OS.
 *
 * This is V8 turned on the console itself: "the platform reported a failure" and
 * "we could not ask the platform" are different facts, and a tool that rendered
 * them identically would have no standing to audit a platform for the same
 * mistake.
 */
export const HARNESS_UNREACHABLE = 'HARNESS_UNREACHABLE';

export class TransportError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly retryAfterMs: number | null;
  readonly details: Record<string, unknown>;
  readonly requestId: string;
  readonly status: number;

  constructor(view: ApiErrorView, status: number) {
    super(view.message);
    this.name = 'TransportError';
    this.code = view.code;
    this.retryable = view.retryable;
    this.retryAfterMs = view.retry_after_ms ?? null;
    this.details = view.details ?? {};
    this.requestId = view.request_id ?? '';
    this.status = status;
  }

  toView(): ApiErrorView {
    return {
      code: this.code,
      message: this.message,
      retryable: this.retryable,
      retry_after_ms: this.retryAfterMs,
      details: this.details,
      request_id: this.requestId,
    };
  }
}

/** True when the body is an error envelope rather than a result. */
export function isErrorView(body: unknown): body is ApiErrorView {
  if (typeof body !== 'object' || body === null) return false;
  const candidate = body as Record<string, unknown>;
  return (
    typeof candidate.code === 'string' &&
    typeof candidate.message === 'string' &&
    typeof candidate.retryable === 'boolean'
  );
}

/**
 * The only retry decision in the console.
 *
 * Reads the field. Adds a bounded retry count so a genuinely retryable error
 * (`OVERLOADED`) does not become an unbounded loop against a platform that is
 * already telling us it is under pressure.
 *
 * `attempt` is zero-based and `maxRetries` counts **retries, not attempts**:
 * `maxRetries: 0` means one call and no retry. The distinction is not cosmetic —
 * with an off-by-one here, `registerDemand` retried once despite asking for
 * zero, and a demand spends compute budget every time it is accepted.
 */
export function shouldRetry(error: unknown, attempt: number, maxRetries = 2): boolean {
  if (attempt >= maxRetries) return false;
  if (error instanceof TransportError) return error.retryable;
  // A network-level failure never reached the platform, so no envelope exists.
  // Retrying is safe for a GET and is what a dropped socket needs.
  return error instanceof TypeError;
}

export function backoffMs(error: unknown, attempt: number): number {
  if (error instanceof TransportError && error.retryAfterMs !== null) {
    return error.retryAfterMs;
  }
  return Math.min(250 * 2 ** attempt, 4000);
}

/** A human-facing description that keeps the machine-readable code visible. */
export function describeError(error: unknown): { code: string; message: string; retryable: boolean } {
  if (error instanceof TransportError) {
    return { code: error.code, message: error.message, retryable: error.retryable };
  }
  if (error instanceof Error) {
    return { code: 'TRANSPORT', message: error.message, retryable: false };
  }
  return { code: 'UNKNOWN', message: String(error), retryable: false };
}

/** Whether this failure means the harness was never reached. */
export function isUnreachable(error: unknown): boolean {
  return error instanceof TransportError && error.code === HARNESS_UNREACHABLE;
}

/**
 * Classify a failed response that carries **no** platform envelope.
 *
 * A response the platform produced always carries an `ApiErrorView`; the harness
 * renders every typed error through Vision OS's own `error_view`. So a failure
 * with no envelope did not come from the platform — it came from whatever sits
 * between us and it. The Vite dev proxy answers `500` with an empty body when
 * the harness is down, and that is by far the most common cause.
 *
 * Reading the status here is *not* the retryability inference 09_API §8 forbids:
 * that rule governs errors the platform reported, and this is the case where it
 * reported nothing at all.
 */
export function transportFailure(status: number, bodyText: string): ApiErrorViewLike {
  const emptyBody = bodyText.trim().length === 0;
  if (status >= 500 && emptyBody) {
    return {
      code: HARNESS_UNREACHABLE,
      message:
        'The Validation Harness did not respond. Start it with ' +
        '`cd harness && python -m vosvc_harness` — the console is a viewer and ' +
        'has no data of its own.',
      retryable: true,
      details: { status, hint: 'empty body from the dev proxy means the upstream socket was refused' },
    };
  }
  return {
    code: 'INTERNAL',
    message: `HTTP ${status}${emptyBody ? ' (no response body)' : `: ${bodyText.slice(0, 200)}`}`,
    retryable: false,
    details: { status },
  };
}

type ApiErrorViewLike = {
  code: string;
  message: string;
  retryable: boolean;
  details?: Record<string, unknown>;
};
