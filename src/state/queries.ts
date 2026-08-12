/**
 * React Query bindings.
 *
 * Every hook is a read. There are exactly three mutations in the console —
 * transport commands, fault arming, and demand registration — and none of them
 * writes a fact: two drive a video source, and the third registers *influence*
 * over what the platform chooses to compute (§M14).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  ReportKind,
  ScenarioName,
  TransportAction,
} from '@contract/types';
import { useConsole } from './ConsoleProvider';

export const keys = {
  health: ['health'] as const,
  media: ['media'] as const,
  sessions: ['sessions'] as const,
  session: (id: string) => ['session', id] as const,
  architecture: (id?: string) => ['architecture', id ?? 'none'] as const,
  metrics: (id?: string) => ['metrics', id ?? 'none'] as const,
  capabilities: (id?: string) => ['capabilities', id ?? 'none'] as const,
  state: (id?: string) => ['state', id ?? 'none'] as const,
  observations: (id?: string) => ['observations', id ?? 'none'] as const,
  demands: (id?: string) => ['demands', id ?? 'none'] as const,
  faults: (id?: string) => ['faults', id ?? 'none'] as const,
  frames: (id?: string) => ['frames', id ?? 'none'] as const,
  replay: (id?: string) => ['replay', id ?? 'none'] as const,
  report: (kind: ReportKind, id?: string, baseline?: string) =>
    ['report', kind, id ?? 'none', baseline ?? 'none'] as const,
};

export function useHealth() {
  const { client } = useConsole();
  return useQuery({
    queryKey: keys.health,
    queryFn: () => client.health(),
    refetchInterval: 5000,
  });
}

export function useMedia() {
  const { client } = useConsole();
  return useQuery({ queryKey: keys.media, queryFn: () => client.listMedia() });
}

export function useSessions() {
  const { client } = useConsole();
  return useQuery({
    queryKey: keys.sessions,
    queryFn: () => client.listSessions(),
    refetchInterval: 4000,
  });
}

export function useArchitecture() {
  const { client, sessionId } = useConsole();
  return useQuery({
    queryKey: keys.architecture(sessionId ?? undefined),
    queryFn: () => client.architecture(sessionId ?? undefined),
    refetchInterval: 6000,
  });
}

export function useMetrics() {
  const { client, sessionId } = useConsole();
  return useQuery({
    queryKey: keys.metrics(sessionId ?? undefined),
    queryFn: () => client.metrics(sessionId ?? undefined),
    refetchInterval: 2000,
    enabled: Boolean(sessionId),
  });
}

export function useVisionState() {
  const { client, sessionId, revision } = useConsole();
  return useQuery({
    // `revision` participates in the key so a state query re-runs as the replay
    // advances. Vision State is a projection: the answer legitimately changes
    // between frames, and a cached one would show the engineer a stale world.
    queryKey: [...keys.state(sessionId ?? undefined), Math.floor(revision / 8)],
    queryFn: () =>
      client.queryState({
        session_id: sessionId,
        filter: { lifecycle: ['active', 'occluded', 'provisional'] },
        options: { include_trajectory: true, include_provenance: true, limit: 200 },
      }),
    enabled: Boolean(sessionId),
  });
}

/**
 * The platform's window policy bound — `ApiLimits.max_window_ms`, 24 hours.
 *
 * A wider request is rejected with `WINDOW_TOO_LARGE`: *"Reject with a bound and
 * a cursor rather than degrading the service for everyone."*
 */
const MAX_WINDOW_NS = 86_400_000 * 1_000_000;

/**
 * The window a validation session's observations actually live in.
 *
 * **A validation session runs on a `VirtualClock` that starts at zero**, so
 * `t_capture` is measured from the start of the replay, not from the wall clock.
 * Observations land a few hundred milliseconds after the Unix epoch, decades
 * away from `Date.now()`.
 *
 * That made the original default wrong twice over. `0 → Date.now()` is 56 years
 * against a 24-hour bound, so every query failed with `WINDOW_TOO_LARGE`;
 * narrowing it to "the last 15 minutes of wall time" then returned `200` with
 * **zero results, forever** — which is far worse, because an empty page that
 * really means "you asked about the wrong century" is indistinguishable from
 * "nothing was observed". That is precisely the conflation V8 exists to prevent.
 *
 * So the window is derived from the replay's own timeline: frame count over
 * frame rate, doubled for headroom, clamped to the policy bound. Arithmetic on
 * values the session handed us — not a judgment about what any of them mean.
 */
function replayWindow(frameCount: number, targetFps: number): { start_ns: number; end_ns: number } {
  const spanNs = (frameCount / Math.max(targetFps, 0.1)) * 1_000_000_000;
  return { start_ns: 0, end_ns: Math.min(Math.ceil(spanNs * 2) + 1_000_000_000, MAX_WINDOW_NS) };
}

export function useObservationQuery(windowNs: { start_ns: number; end_ns: number } | null) {
  const { client, sessionId, session } = useConsole();
  const fallback = replayWindow(session?.frame_count ?? 0, session?.target_fps ?? 12);
  const window = windowNs ?? fallback;

  return useQuery({
    queryKey: [...keys.observations(sessionId ?? undefined), window.start_ns, window.end_ns],
    queryFn: () =>
      client.queryObservations({ session_id: sessionId, window, limit: 500 }),
    enabled: Boolean(sessionId),
  });
}

export { MAX_WINDOW_NS, replayWindow };

export function useDemands() {
  const { client, sessionId } = useConsole();
  return useQuery({
    queryKey: keys.demands(sessionId ?? undefined),
    queryFn: () => client.listDemands(sessionId ?? undefined),
    enabled: Boolean(sessionId),
    refetchInterval: 5000,
  });
}

export function useFaults() {
  const { client, sessionId } = useConsole();
  return useQuery({
    queryKey: keys.faults(sessionId ?? undefined),
    queryFn: () => client.listFaults(sessionId!),
    enabled: Boolean(sessionId),
    refetchInterval: 3000,
  });
}

export function useFrameLedger() {
  const { client, sessionId, revision } = useConsole();
  return useQuery({
    queryKey: [...keys.frames(sessionId ?? undefined), Math.floor(revision / 16)],
    queryFn: () => client.frameLedger(sessionId!, 0, 2000),
    enabled: Boolean(sessionId),
  });
}

export function useReplayVerification() {
  const { client, sessionId } = useConsole();
  return useQuery({
    queryKey: keys.replay(sessionId ?? undefined),
    queryFn: () => client.verifyReplay(sessionId ?? undefined),
    enabled: Boolean(sessionId),
    // Not polled. `verify_replay` reprojects every partition from the log; a
    // background poll would make the console the platform's heaviest consumer.
    staleTime: Infinity,
  });
}

export function useReport(kind: ReportKind, baselineSessionId?: string) {
  const { client, sessionId } = useConsole();
  return useQuery({
    queryKey: keys.report(kind, sessionId ?? undefined, baselineSessionId),
    queryFn: () =>
      client.report(kind, { sessionId: sessionId ?? undefined, baselineSessionId }),
    enabled: Boolean(sessionId),
  });
}

// --- mutations ---------------------------------------------------------------- //

export function useTransport() {
  const { client, sessionId, setSession } = useConsole();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      action,
      detail,
    }: {
      action: TransportAction;
      detail?: Record<string, unknown>;
    }) => client.transport(sessionId!, action, detail ?? {}),
    onSuccess: (session) => {
      setSession(session);
      queryClient.invalidateQueries({ queryKey: keys.sessions });
    },
  });
}

export function useArmFault() {
  const { client, sessionId } = useConsole();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      scenario: ScenarioName;
      at_frame?: number;
      duration_frames?: number;
      params?: Record<string, number>;
    }) => client.armFault(sessionId!, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.faults(sessionId ?? undefined) }),
  });
}

export function useClearFaults() {
  const { client, sessionId } = useConsole();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (scenario?: ScenarioName) => client.clearFaults(sessionId!, scenario),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.faults(sessionId ?? undefined) }),
  });
}

export function useCreateSession() {
  const { client, setSessionId, setSession } = useConsole();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<ValidationClientCreate>[0]) => client.createSession(body),
    onSuccess: (session) => {
      setSessionId(session.session_id);
      setSession(session);
      queryClient.invalidateQueries({ queryKey: keys.sessions });
    },
  });
}

type ValidationClientCreate = InstanceType<typeof import('@transport/client').ValidationClient>['createSession'];

export function useUploadMedia() {
  const { client } = useConsole();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => client.uploadMedia(file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.media }),
  });
}
