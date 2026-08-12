/**
 * Test helpers: a fake transport and a rendering wrapper.
 *
 * The fake speaks the wire contract, not the console's internals, so a test that
 * passes here is a test that would pass against the real harness.
 */

import type { ReactElement } from 'react';
import { render, type RenderResult } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '@mui/material';
import { ValidationClient } from '@transport/client';
import { ConsoleProvider } from '@state/ConsoleProvider';
import { theme } from '@/theme/theme';

export interface FakeRoute {
  match: (url: string, init?: RequestInit) => boolean;
  status?: number;
  body: unknown | ((url: string, init?: RequestInit) => unknown);
}

export function fakeFetch(routes: FakeRoute[]): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const route = routes.find((candidate) => candidate.match(url, init));
    if (!route) {
      return new Response(
        JSON.stringify({
          code: 'NOT_FOUND',
          message: `no fake route for ${url}`,
          retryable: false,
        }),
        { status: 404, headers: { 'Content-Type': 'application/json' } },
      );
    }
    const body = typeof route.body === 'function' ? (route.body as (u: string, i?: RequestInit) => unknown)(url, init) : route.body;
    return new Response(body === undefined ? '' : JSON.stringify(body), {
      status: route.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;
}

export function clientWith(routes: FakeRoute[]): ValidationClient {
  return new ValidationClient({ fetchImpl: fakeFetch(routes) });
}

export function get(path: string, body: unknown, status?: number): FakeRoute {
  return {
    match: (url, init) => url.includes(path) && (init?.method ?? 'GET') === 'GET',
    body,
    ...(status === undefined ? {} : { status }),
  };
}

export function post(path: string, body: unknown, status?: number): FakeRoute {
  return {
    match: (url, init) => url.includes(path) && init?.method === 'POST',
    body,
    ...(status === undefined ? {} : { status }),
  };
}

export function renderConsole(
  ui: ReactElement,
  options: { client?: ValidationClient } = {},
): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={queryClient}>
        <ConsoleProvider client={options.client}>{ui}</ConsoleProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

/** A minimal scriptable WebSocket for stream tests. */
export class FakeSocket {
  static instances: FakeSocket[] = [];

  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  emit(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  emitRaw(data: string): void {
    this.onmessage?.({ data } as MessageEvent);
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.();
  }

  static reset(): void {
    FakeSocket.instances = [];
  }
}
