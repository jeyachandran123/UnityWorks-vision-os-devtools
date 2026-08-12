/**
 * Console-wide state: the client, the active session, the live tap buffer.
 *
 * This provider holds **no facts about the world**. It holds a connection, a
 * session id, and a bounded ring of messages the harness sent. Every rendered
 * value in the console traces back to one of those, which is what "no business
 * logic in the Validation Console" means in practice.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type {
  Channel,
  GapPayload,
  SessionDescription,
  TapMessage,
} from '@contract/types';
import { ValidationClient } from '@transport/client';
import { TapBuffer, TapStream, type StreamStatus } from '@transport/stream';

interface ConsoleContextValue {
  client: ValidationClient;
  sessionId: string | null;
  setSessionId: (id: string | null) => void;
  session: SessionDescription | null;
  setSession: (session: SessionDescription | null) => void;
  buffer: TapBuffer;
  /** Bumped on every batch of tap messages, so panels can subscribe cheaply. */
  revision: number;
  streamStatus: StreamStatus;
  gaps: Array<GapPayload & { local: boolean }>;
  latestOf: (channel: Channel) => TapMessage | undefined;
  refreshSession: () => Promise<void>;
}

const ConsoleContext = createContext<ConsoleContextValue | null>(null);

export function ConsoleProvider({
  children,
  client: injected,
}: {
  children: ReactNode;
  client?: ValidationClient;
}) {
  const client = useMemo(() => injected ?? new ValidationClient(), [injected]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [session, setSession] = useState<SessionDescription | null>(null);
  const [revision, setRevision] = useState(0);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>('idle');
  const [gaps, setGaps] = useState<Array<GapPayload & { local: boolean }>>([]);

  const buffer = useMemo(() => new TapBuffer(5000), []);
  const streamRef = useRef<TapStream | null>(null);
  const pending = useRef(0);

  // Messages arrive far faster than React should re-render. Batching on an
  // animation frame keeps the console responsive during a 25 fps replay without
  // dropping anything: every message is in the buffer before the frame fires.
  const flush = useCallback(() => {
    if (pending.current === 0) return;
    pending.current = 0;
    setRevision((r) => r + 1);
  }, []);

  useEffect(() => {
    if (!sessionId) {
      streamRef.current?.close();
      streamRef.current = null;
      setStreamStatus('idle');
      return;
    }

    buffer.clear();
    setGaps([]);

    let frame = 0;
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        flush();
      });
    };

    const stream = new TapStream(sessionId, {
      onMessage: (message) => {
        buffer.push(message);
        pending.current += 1;
        schedule();
      },
      onGap: (gap) => setGaps((held) => [...held, gap]),
      onStatus: (status) => setStreamStatus(status),
    });

    streamRef.current = stream;
    stream.connect();

    return () => {
      if (frame) cancelAnimationFrame(frame);
      stream.close();
      streamRef.current = null;
    };
  }, [sessionId, buffer, flush]);

  const refreshSession = useCallback(async () => {
    if (!sessionId) return;
    try {
      setSession(await client.getSession(sessionId));
    } catch {
      // A session that has gone away is a state the UI renders, not a crash.
    }
  }, [client, sessionId]);

  const latestOf = useCallback(
    (channel: Channel) => buffer.latest(channel),
    // `revision` is the dependency that matters: the buffer object is stable and
    // mutates in place, so without this the memo would never invalidate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [buffer, revision],
  );

  const value = useMemo<ConsoleContextValue>(
    () => ({
      client,
      sessionId,
      setSessionId,
      session,
      setSession,
      buffer,
      revision,
      streamStatus,
      gaps,
      latestOf,
      refreshSession,
    }),
    [client, sessionId, session, buffer, revision, streamStatus, gaps, latestOf, refreshSession],
  );

  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}

export function useConsole(): ConsoleContextValue {
  const value = useContext(ConsoleContext);
  if (!value) throw new Error('useConsole must be used inside <ConsoleProvider>');
  return value;
}

/** Messages on one channel, re-read whenever the buffer advances. */
export function useChannel(channel: Channel): TapMessage[] {
  const { buffer, revision } = useConsole();
  return useMemo(
    () => buffer.get(channel),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [buffer, channel, revision],
  );
}
