/**
 * The REST client — the console's only door to Vision OS.
 *
 * Every method here is a `fetch`. There is no other way for this application to
 * learn anything, which is what makes "communication occurs only through public
 * APIs" a structural property rather than a promise:
 * `tests/architecture/no-backend-imports.test.ts` walks `src/` and fails on any
 * import that resolves outside this repository.
 *
 * Note what is absent: no `updateObject`, no `setAttribute`, no
 * `deleteObservation`. Not commented out, not permission-gated — never written,
 * because `ObservationApi` exposes nothing to call (V6).
 */

import type {
  ApiErrorView,
  ArchitectureReport,
  CapabilitySummary,
  CoverageReport,
  DemandView,
  EvidenceView,
  FaultVerdict,
  FrameLedgerEntry,
  HarnessHealth,
  MediaAsset,
  MetricsResponse,
  ObjectView,
  ObservationPage,
  ReplayVerification,
  ReportEnvelope,
  ReportKind,
  ScenarioName,
  SessionDescription,
  StateResult,
  TapMessage,
  TapStats,
  TransportAction,
} from '@contract/types';
import {
  HARNESS_UNREACHABLE,
  TransportError,
  backoffMs,
  isErrorView,
  shouldRetry,
  transportFailure,
} from './errors';

export interface ClientOptions {
  baseUrl?: string;
  acceptMajor?: number;
  fetchImpl?: typeof fetch;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class ValidationClient {
  private readonly baseUrl: string;
  private readonly acceptMajor: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? '').replace(/\/$/, '');
    this.acceptMajor = options.acceptMajor ?? 1;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  // --- core --------------------------------------------------------------- //

  private async request<T>(
    path: string,
    init: RequestInit & { retries?: number } = {},
  ): Promise<T> {
    const { retries = 2, ...rest } = init;
    const url = `${this.baseUrl}/api/v1${path}`;
    let attempt = 0;

    for (;;) {
      try {
        const response = await this.fetchImpl(url, {
          ...rest,
          headers: {
            'X-VOS-Accept-Major': String(this.acceptMajor),
            ...(rest.body instanceof FormData
              ? {}
              : { 'Content-Type': 'application/json' }),
            ...(rest.headers ?? {}),
          },
        });

        const text = await response.text();
        let body: unknown = null;
        try {
          body = text ? JSON.parse(text) : null;
        } catch {
          // A non-JSON body never came from the harness, which answers JSON on
          // every path including failures. Leave it null and let the classifier
          // below name it for what it is.
          body = null;
        }

        if (!response.ok) {
          throw new TransportError(
            isErrorView(body) ? body : transportFailure(response.status, text),
            response.status,
          );
        }

        // The harness answers 200 with an error envelope for "no session yet",
        // which is a state rather than a failure. Surfacing it as a typed error
        // lets callers distinguish it from an empty result — which is the whole
        // point of V8.
        if (isErrorView(body)) {
          throw new TransportError(body as ApiErrorView, response.status);
        }

        return body as T;
      } catch (error) {
        // `retries` counts retries, not attempts: 0 means this call and no more.
        if (!shouldRetry(error, attempt, retries)) {
          // A bare TypeError from `fetch` is a refused or aborted socket — the
          // request never reached the harness. Give it the same code as an
          // empty-bodied proxy 500 so the UI has exactly one condition to
          // recognise for "the harness is not there".
          if (error instanceof TypeError) {
            throw new TransportError(
              {
                code: HARNESS_UNREACHABLE,
                message:
                  'Could not reach the Validation Harness. Start it with ' +
                  '`cd harness && python -m vosvc_harness`.',
                retryable: true,
                details: { cause: error.message },
              },
              0,
            );
          }
          throw error;
        }
        await sleep(backoffMs(error, attempt));
        attempt += 1;
      }
    }
  }

  private get<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: 'GET' });
  }

  private post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
      // A POST is not retried by default: `register_demand` spends money and
      // causes computation (12_SECURITY §5.3 — *"Demands... are not a read"*).
      retries: 0,
    });
  }

  // --- health, capability, architecture ------------------------------------ //

  health(): Promise<HarnessHealth> {
    return this.get('/health');
  }

  capabilities(sessionId?: string): Promise<CapabilitySummary & { unavailable?: string }> {
    return this.get(`/capabilities${query({ session_id: sessionId })}`);
  }

  architecture(sessionId?: string): Promise<ArchitectureReport> {
    return this.get(`/architecture${query({ session_id: sessionId })}`);
  }

  metrics(sessionId?: string): Promise<MetricsResponse> {
    return this.get(`/metrics${query({ session_id: sessionId })}`);
  }

  // --- media ---------------------------------------------------------------- //

  listMedia(): Promise<{ media: MediaAsset[]; capabilities: HarnessHealth['media'] }> {
    return this.get('/media');
  }

  uploadMedia(file: File): Promise<MediaAsset> {
    const form = new FormData();
    form.append('file', file);
    return this.request('/media', { method: 'POST', body: form, retries: 0 });
  }

  deleteMedia(mediaId: string): Promise<void> {
    return this.request(`/media/${encodeURIComponent(mediaId)}`, {
      method: 'DELETE',
      retries: 0,
    });
  }

  // --- sessions -------------------------------------------------------------- //

  listSessions(): Promise<{ sessions: SessionDescription[] }> {
    return this.get('/sessions');
  }

  getSession(sessionId: string): Promise<SessionDescription> {
    return this.get(`/sessions/${encodeURIComponent(sessionId)}`);
  }

  createSession(body: {
    media_id: string;
    camera_id?: string;
    tenant_id?: string;
    semantics?: 'archival' | 'realtime';
    target_fps?: number;
    deterministic?: boolean;
    autostart?: boolean;
    rtsp?: boolean;
  }): Promise<SessionDescription> {
    return this.post('/sessions', body);
  }

  deleteSession(sessionId: string): Promise<void> {
    return this.request(`/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
      retries: 0,
    });
  }

  transport(
    sessionId: string,
    action: TransportAction,
    detail: Record<string, unknown> = {},
  ): Promise<SessionDescription> {
    return this.post(`/sessions/${encodeURIComponent(sessionId)}/transport`, {
      action,
      ...detail,
    });
  }

  frameLedger(
    sessionId: string,
    offset = 0,
    limit = 500,
  ): Promise<{ total: number; offset: number; entries: FrameLedgerEntry[]; frame_count: number }> {
    return this.get(
      `/sessions/${encodeURIComponent(sessionId)}/frames${query({ offset, limit })}`,
    );
  }

  /**
   * The URL of one decoded frame.
   *
   * Requires a `purpose`, which is recorded. Frame serving is off by default —
   * pixels stay local unless a deployment decides otherwise (V12,
   * `docs/CONTRACT.md` §4.3).
   */
  frameUrl(sessionId: string, index: number, purpose: string): string {
    return `${this.baseUrl}/api/v1/sessions/${encodeURIComponent(sessionId)}/frames/${index}${query(
      { purpose },
    )}`;
  }

  // --- taps ------------------------------------------------------------------- //

  taps(
    sessionId: string,
    options: { channels?: string[]; sinceSeq?: number; limit?: number } = {},
  ): Promise<{ records: TapMessage[]; stats: TapStats; channels: string[] }> {
    return this.get(
      `/sessions/${encodeURIComponent(sessionId)}/taps${query({
        channels: options.channels?.join(','),
        since_seq: options.sinceSeq,
        limit: options.limit,
      })}`,
    );
  }

  // --- faults ------------------------------------------------------------------ //

  listFaults(
    sessionId: string,
  ): Promise<{ armed: FaultVerdict[]; scenarios: Array<{ scenario: string; stage: string }> }> {
    return this.get(`/sessions/${encodeURIComponent(sessionId)}/faults`);
  }

  armFault(
    sessionId: string,
    body: {
      scenario: ScenarioName;
      at_frame?: number;
      duration_frames?: number;
      params?: Record<string, number>;
    },
  ): Promise<{ armed: FaultVerdict[]; restarted?: boolean }> {
    return this.post(`/sessions/${encodeURIComponent(sessionId)}/faults`, body);
  }

  clearFaults(sessionId: string, scenario?: ScenarioName): Promise<{ armed: FaultVerdict[] }> {
    return this.post(`/sessions/${encodeURIComponent(sessionId)}/faults`, {
      clear: true,
      scenario,
    });
  }

  // --- the Observation API (read-only) ------------------------------------------ //

  queryState(body: Record<string, unknown> = {}): Promise<StateResult> {
    return this.post('/state/query', body);
  }

  queryObservations(body: Record<string, unknown> = {}): Promise<ObservationPage> {
    return this.post('/observations/query', body);
  }

  getObject(objectId: string, sessionId?: string): Promise<ObjectView> {
    return this.get(`/objects/${encodeURIComponent(objectId)}${query({ session_id: sessionId })}`);
  }

  coverage(body: Record<string, unknown> = {}): Promise<CoverageReport> {
    return this.post('/coverage', body);
  }

  /** `purpose` is required and has no default. See `docs/CONTRACT.md` §2.3. */
  getEvidence(blobRef: string, purpose: string, sessionId?: string): Promise<EvidenceView> {
    return this.get(
      `/evidence/${encodeURIComponent(blobRef)}${query({ purpose, session_id: sessionId })}`,
    );
  }

  listDemands(sessionId?: string): Promise<{ demands: DemandView[]; unavailable?: string }> {
    return this.get(`/demands${query({ session_id: sessionId })}`);
  }

  registerDemand(body: Record<string, unknown>): Promise<DemandView> {
    return this.post('/demands', body);
  }

  revokeDemand(demandId: string, sessionId?: string): Promise<void> {
    return this.request(
      `/demands/${encodeURIComponent(demandId)}${query({ session_id: sessionId })}`,
      { method: 'DELETE', retries: 0 },
    );
  }

  // --- verification and reports --------------------------------------------------- //

  verifyReplay(sessionId?: string): Promise<ReplayVerification> {
    return this.post('/replay/verify', { session_id: sessionId });
  }

  report(
    kind: ReportKind,
    options: { sessionId?: string; baselineSessionId?: string } = {},
  ): Promise<ReportEnvelope> {
    return this.get(
      `/reports/${kind}${query({
        session_id: options.sessionId,
        baseline_session_id: options.baselineSessionId,
      })}`,
    );
  }
}

function query(params: Record<string, string | number | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== '')
    .map(([key, value]) => `${key}=${encodeURIComponent(String(value))}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

export const client = new ValidationClient();
