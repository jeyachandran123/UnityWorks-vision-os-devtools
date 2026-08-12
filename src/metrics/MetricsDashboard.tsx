/**
 * Performance dashboard.
 *
 * Every number comes from the Metrics Engine's closed vocabulary. The console
 * does not time anything itself: wrapping a module to measure it would change
 * what is being measured and would require touching Vision OS. The histograms
 * the platform already records are both more accurate and free.
 *
 * The sweep timestamp is recorded separately from the values, so a **missed**
 * cadence is visible rather than assumed. A dashboard that plotted samples at
 * their nominal interval would hide exactly the stall an engineer is hunting.
 */

import { useMemo } from 'react';
import { Box, Chip, Divider, Stack, Typography } from '@mui/material';
import { useChannel, useConsole } from '@state/ConsoleProvider';
import { useMetrics } from '@state/queries';
import {
  DataTable,
  EmptyResult,
  Mono,
  PanelShell,
  Row,
  StatTile,
  Unavailable,
} from '@components/primitives';
import { mono, semantic } from '@/theme/theme';

/** Module → the metric families 05_MODULES assigns it. Names, not thresholds. */
const MODULE_METRICS: Array<{ module: string; prefixes: string[] }> = [
  { module: 'acquisition', prefixes: ['vision_os.frames', 'vision_os.decode', 'vision_os.source', 'vision_os.buffer', 'vision_os.scheduler', 'vision_os.privacy'] },
  { module: 'detection', prefixes: ['vision_os.detection'] },
  { module: 'tracking', prefixes: ['vision_os.tracking'] },
  { module: 'registry', prefixes: ['vision_os.registry'] },
  { module: 'cropping', prefixes: ['vision_os.cropping'] },
  { module: 'understanding', prefixes: ['vision_os.understanding'] },
  { module: 'synthesis', prefixes: ['vision_os.synthesis'] },
  { module: 'state', prefixes: ['vision_os.state'] },
  { module: 'api', prefixes: ['vision_os.api'] },
  { module: 'models', prefixes: ['vision_os.models'] },
  { module: 'coverage', prefixes: ['vision_os.coverage'] },
  { module: 'replay', prefixes: ['vision_os.replay'] },
];

export function MetricsDashboard() {
  const { sessionId, session, buffer, revision } = useConsole();
  const { data, error } = useMetrics();
  const samples = useChannel('metrics');

  const flat = useMemo(() => flatten(data?.sample?.values ?? {}), [data]);

  const observedFps = useMemo(() => {
    const frames = buffer.get('acquisition');
    if (frames.length < 2) return null;
    const first = frames[0]!;
    const last = frames[frames.length - 1]!;
    const seconds = (last.ts_ns - first.ts_ns) / 1e9;
    return seconds > 0 ? (frames.length - 1) / seconds : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buffer, revision]);

  const cadence = useMemo(() => {
    if (samples.length < 2) return null;
    const deltas: number[] = [];
    for (let i = 1; i < samples.length; i += 1) {
      deltas.push((samples[i]!.ts_ns - samples[i - 1]!.ts_ns) / 1e6);
    }
    deltas.sort((a, b) => a - b);
    return {
      median: deltas[Math.floor(deltas.length / 2)] ?? 0,
      worst: deltas[deltas.length - 1] ?? 0,
    };
  }, [samples]);

  if (!sessionId) {
    return (
      <PanelShell title="Performance">
        <Unavailable what="Metrics" reason="No session is open." />
      </PanelShell>
    );
  }

  if (error || data?.available === false) {
    return (
      <PanelShell title="Performance">
        <Unavailable what="Metrics" reason={data?.reason ?? String(error)} />
      </PanelShell>
    );
  }

  if (data?.sample?.unavailable) {
    return (
      <PanelShell title="Performance">
        <Unavailable what="Metrics" reason={data.sample.unavailable} />
      </PanelShell>
    );
  }

  return (
    <PanelShell
      title="Performance"
      subtitle={`${data?.names.length ?? 0} metric names in the closed vocabulary`}
    >
      <Stack spacing={1.5}>
        <Row>
          <StatTile
            label="observed fps"
            value={observedFps ? observedFps.toFixed(2) : '—'}
            hint="Measured from tap arrival times, not from the configured target. A gap between the two is the finding."
          />
          <StatTile label="target fps" value={session?.target_fps ?? '—'} />
          <StatTile label="replay speed" value={`${session?.speed ?? 1}×`} />
          <StatTile label="frames emitted" value={data?.frames_emitted ?? 0} />
          <StatTile
            label="tap messages"
            value={data?.taps?.sequence ?? 0}
            hint="Global monotonic sequence. Loss is detectable because this never skips."
          />
          <StatTile
            label="tap drops"
            value={data?.taps?.dropped ?? 0}
            tone={(data?.taps?.dropped ?? 0) > 0 ? 'warn' : 'good'}
            hint="Harness-side drops. Every one produces a gap on the stream."
          />
        </Row>

        <Row>
          <StatTile
            label="sweep median"
            value={cadence ? cadence.median.toFixed(0) : '—'}
            unit="ms"
            hint="Actual spacing between metric sweeps."
          />
          <StatTile
            label="sweep worst"
            value={cadence ? cadence.worst.toFixed(0) : '—'}
            unit="ms"
            tone={cadence && cadence.worst > cadence.median * 4 ? 'warn' : 'default'}
            hint="A worst case far above the median means the harness or the platform stalled. The dashboard shows real spacing rather than assuming the nominal cadence was met."
          />
          <StatTile label="samples" value={samples.length} />
        </Row>

        <Divider />

        <Typography variant="h3">Per-module execution</Typography>
        {flat.length === 0 ? (
          <EmptyResult
            what="metric values"
            note="The Metrics Engine reported no readable values in this build. That is a capability gap, not a zero."
          />
        ) : (
          <Stack spacing={1}>
            {MODULE_METRICS.map(({ module, prefixes }) => {
              const rows = flat.filter((row) => prefixes.some((p) => row.key.startsWith(p)));
              if (rows.length === 0) return null;
              return (
                <Box key={module}>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                    <Typography sx={{ fontFamily: mono, fontSize: '0.78rem', fontWeight: 600 }}>
                      {module}
                    </Typography>
                    <Chip size="small" label={`${rows.length} metrics`} />
                  </Stack>
                  <DataTable
                    rows={rows}
                    columns={[
                      { key: 'name', header: 'metric', render: (r) => <Mono>{r.key}</Mono> },
                      {
                        key: 'value',
                        header: 'value',
                        width: 160,
                        render: (r) => <Mono colour={valueColour(r.key)}>{format(r.value)}</Mono>,
                      },
                    ]}
                    emptyWhat="values"
                    rowKey={(r) => r.key}
                  />
                </Box>
              );
            })}
          </Stack>
        )}

        <Divider />
        <Typography variant="caption">
          <strong>vision_os.replay.mismatches must be zero.</strong> A non-zero value means a rebuild
          produced a different world from the live run, which invalidates every recovery guarantee in
          07_STATE §9.1.
        </Typography>
      </Stack>
    </PanelShell>
  );
}

/** Metrics whose own documentation says a non-zero value is a problem. */
const MUST_BE_ZERO = [
  'vision_os.replay.mismatches',
  'vision_os.api.audit_failures',
  'vision_os.runtime.consumer_failures',
  'vision_os.tracking.out_of_order_frames',
];

function valueColour(key: string): string | undefined {
  return MUST_BE_ZERO.includes(key) ? semantic.degraded : undefined;
}

interface FlatRow {
  key: string;
  value: unknown;
}

function flatten(value: unknown, prefix = ''): FlatRow[] {
  if (value === null || value === undefined) return [];
  if (typeof value !== 'object') return prefix ? [{ key: prefix, value }] : [];
  if (Array.isArray(value)) return prefix ? [{ key: prefix, value }] : [];

  const out: FlatRow[] = [];
  for (const [key, held] of Object.entries(value as Record<string, unknown>)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (held !== null && typeof held === 'object' && !Array.isArray(held)) {
      out.push(...flatten(held, next));
    } else {
      out.push({ key: next, value: held });
    }
  }
  return out;
}

function format(value: unknown): string {
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : value.toFixed(3);
  }
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
