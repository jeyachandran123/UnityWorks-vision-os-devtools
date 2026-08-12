/**
 * Architecture validation screens.
 *
 * Module health, pipeline ordering, ownership transfer, provenance, Semantic
 * Ceiling compliance, evidence traceability — every one read from the running
 * platform rather than from the documentation. A screen that restated the specs
 * would validate the specs; these compare the specs against what the system
 * actually did.
 */

import { useMemo } from 'react';
import { Alert, Chip, Divider, Paper, Stack, Typography } from '@mui/material';
import { useChannel, useConsole } from '@state/ConsoleProvider';
import { useArchitecture, useReplayVerification } from '@state/queries';
import {
  DataTable,
  EmptyResult,
  JsonView,
  Mono,
  PanelShell,
  Row,
  StatTile,
  Unavailable,
  VerdictChip,
} from '@components/primitives';
import { semantic } from '@/theme/theme';
import type { Observation } from '@contract/types';

export function ArchitectureValidation() {
  const { sessionId } = useConsole();
  const { data, error } = useArchitecture();
  const replay = useReplayVerification();
  const observations = useChannel('observation');

  const ceiling = useMemo(() => auditCeiling(observations), [observations]);

  if (!sessionId) {
    return (
      <PanelShell title="Architecture Validation">
        <Unavailable what="Architecture validation" reason="No session is open." />
      </PanelShell>
    );
  }

  if (error || !data) {
    return (
      <PanelShell title="Architecture Validation">
        <Unavailable what="Architecture validation" reason={String(error ?? 'no report')} />
      </PanelShell>
    );
  }

  const runtime = data.runtime;
  const inversions = (runtime.observed_order ?? []).filter((o) => o.out_of_order);
  const mismatches = replay.data?.mismatches;

  return (
    <PanelShell title="Architecture Validation" subtitle={data.vision_os.api_version ?? ''}>
      <Stack spacing={2}>
        {/* --- headline verdicts --- */}
        <Row>
          <StatTile
            label="Vision OS"
            value={data.vision_os.available ? 'loaded' : 'UNAVAILABLE'}
            tone={data.vision_os.available ? 'good' : 'bad'}
          />
          <StatTile
            label="pipeline ordering"
            value={inversions.length === 0 ? 'consistent' : `${inversions.length} inverted`}
            tone={inversions.length === 0 ? 'good' : 'bad'}
            hint="A layer producing output before the layer that must feed it would mean a bypass."
          />
          <StatTile
            label="replay mismatches"
            value={mismatches === undefined ? '—' : mismatches}
            tone={mismatches === undefined ? 'unknown' : mismatches === 0 ? 'good' : 'bad'}
            hint="V13. A non-zero value invalidates every recovery guarantee in 07_STATE §9.1."
          />
          <StatTile
            label="ceiling violations"
            value={ceiling.violations.length}
            tone={ceiling.violations.length === 0 ? 'good' : 'bad'}
            hint="Observations carrying a field that looks like a business verdict rather than a visible fact."
          />
          <StatTile
            label="untraceable observations"
            value={ceiling.untraceable}
            tone={ceiling.untraceable === 0 ? 'good' : 'bad'}
            hint="V4: no observation without evidence and provenance."
          />
        </Row>

        {!data.vision_os.available ? (
          <Unavailable what="Vision OS" reason={data.vision_os.error} />
        ) : null}

        {/* --- module health --- */}
        <Section title="Module health">
          {runtime.available && runtime.health ? (
            <Row>
              {Object.entries(runtime.health).map(([component, state]) => (
                <StatTile
                  key={component}
                  label={component}
                  value={state}
                  tone={healthTone(state)}
                />
              ))}
            </Row>
          ) : (
            <Unavailable what="Module health" reason={runtime.reason} />
          )}
          {runtime.started_layers?.length ? (
            <Typography variant="caption" sx={{ display: 'block', mt: 1 }}>
              Started layers: <Mono>{runtime.started_layers.join(', ')}</Mono>. A layer this system
              was never given is a layer that silently does nothing — which is why the platform
              reports which ones <em>actually</em> started.
            </Typography>
          ) : null}
        </Section>

        {/* --- pipeline ordering --- */}
        <Section title="Pipeline ordering">
          {runtime.observed_order?.length ? (
            <DataTable
              rows={runtime.observed_order}
              columns={[
                { key: 'channel', header: 'layer', width: 160, render: (r) => <Mono>{r.channel}</Mono> },
                { key: 'seq', header: 'first output at seq', width: 160, render: (r) => <Mono>{r.first_seq}</Mono> },
                {
                  key: 'declared',
                  header: 'declared position',
                  width: 150,
                  render: (r) => <Mono>{r.declared_position ?? '—'}</Mono>,
                },
                {
                  key: 'verdict',
                  header: 'verdict',
                  render: (r) => <VerdictChip verdict={r.out_of_order ? 'fail' : 'pass'} />,
                },
              ]}
              emptyWhat="observed ordering"
              rowKey={(r) => r.channel}
            />
          ) : (
            <EmptyResult what="ordering observations yet" note="Run the replay to populate this." />
          )}
        </Section>

        {/* --- ownership transfer --- */}
        <Section title="Ownership transfer (V10 — layered identity)">
          <DataTable
            rows={data.ownership ?? []}
            columns={[
              { key: 'artifact', header: 'artifact', width: 170, render: (r) => <Mono>{r.artifact}</Mono> },
              { key: 'minted', header: 'minted by', width: 220, render: (r) => <Mono>{r.minted_by}</Mono> },
              {
                key: 'consumed',
                header: 'consumed by',
                render: (r) => <Mono>{r.consumed_by.join(' → ')}</Mono>,
              },
              {
                key: 'note',
                header: 'note',
                render: (r) => (r.note ? <Mono colour={semantic.degraded}>{r.note}</Mono> : null),
              },
            ]}
            emptyWhat="ownership records"
            rowKey={(r) => r.artifact}
          />
          <Typography variant="caption" sx={{ display: 'block', mt: 0.75 }}>
            Detection ≠ track ≠ object. Identity is a revisable assertion, and only the Object
            Registry may mint an object id.
          </Typography>
        </Section>

        {/* --- ports --- */}
        <Section title="Port bindings">
          {data.ports.available ? (
            <>
              <Row>
                <StatTile label="catalogue" value={data.ports.catalogue_size ?? 0} />
                <StatTile label="bindable" value={data.ports.bindable_count ?? 0} />
                <StatTile
                  label="deliberately unbindable"
                  value={data.ports.unbindable?.length ?? 0}
                  hint="The biometric capabilities and the unimplemented modules' ports. These staying unbound is a security property, not just a completeness one."
                />
              </Row>
              {data.ports.unbindable?.length ? (
                <Alert severity="info" variant="outlined" sx={{ mt: 1 }}>
                  Unbindable: <Mono>{data.ports.unbindable.join(', ')}</Mono>
                </Alert>
              ) : null}
            </>
          ) : (
            <Unavailable what="Port catalogue" reason={data.ports.reason} />
          )}
        </Section>

        {/* --- semantic ceiling --- */}
        <Section title="Semantic Ceiling compliance (V1)">
          {ceiling.violations.length === 0 ? (
            <Alert severity="success" variant="outlined">
              No observation carried a field resembling a business verdict. The platform reported
              what was visible and stopped there.
            </Alert>
          ) : (
            <Alert severity="error" variant="outlined">
              <strong>{ceiling.violations.length} suspected ceiling violations.</strong> These
              observations carry keys that look like interpretation rather than observation:
              <JsonView value={ceiling.violations.slice(0, 20)} maxHeight={180} />
            </Alert>
          )}
          <Typography variant="caption" sx={{ display: 'block', mt: 0.75 }}>
            The console flags <em>suspects</em>, never verdicts. Whether a key is business logic is a
            judgment; the platform's own attribute registry is the authority, and this screen exists
            to point an engineer at what to check.
          </Typography>
        </Section>

        {/* --- determinism --- */}
        <Section title="Deterministic replay (V13)">
          {replay.data?.available ? (
            <>
              <Row>
                <StatTile
                  label="verdict"
                  value={replay.data.deterministic ? 'deterministic' : 'MISMATCH'}
                  tone={replay.data.deterministic ? 'good' : 'bad'}
                />
                <StatTile label="mismatches" value={replay.data.mismatches ?? 0} />
                <StatTile label="partitions verified" value={replay.data.reports?.length ?? 0} />
              </Row>
              <JsonView value={replay.data.reports} maxHeight={200} />
            </>
          ) : (
            <Unavailable what="Replay verification" reason={replay.data?.reason} />
          )}
          <Typography variant="caption" sx={{ display: 'block', mt: 0.75 }}>
            Runs the platform's own <Mono>verify_replay</Mono>: the same projection over the same
            log, diffed field by field. The console reports the result unmodified.
          </Typography>
        </Section>

        {/* --- invariants --- */}
        <Section title="The 13 invariants">
          <DataTable
            rows={data.invariants}
            columns={[
              { key: 'id', header: '', width: 50, render: (r) => <Chip size="small" label={r.id} /> },
              { key: 'name', header: 'invariant', width: 220, render: (r) => <Mono>{r.name}</Mono> },
              { key: 'evidence', header: 'what this console checks it against', render: (r) => <Mono colour={semantic.unknown}>{r.evidence}</Mono> },
            ]}
            emptyWhat="invariants"
            rowKey={(r) => r.id}
          />
        </Section>
      </Stack>
    </PanelShell>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Paper elevation={0} sx={{ p: 1.25 }}>
      <Typography variant="h3" sx={{ mb: 1 }}>
        {title}
      </Typography>
      <Divider sx={{ mb: 1 }} />
      {children}
    </Paper>
  );
}

function healthTone(state: string): 'good' | 'warn' | 'bad' | 'unknown' {
  const value = state.toLowerCase();
  if (value.includes('healthy') || value.includes('serving')) return 'good';
  if (value.includes('degraded') || value.includes('stopped')) return 'warn';
  if (value.includes('fail') || value.includes('unhealthy')) return 'bad';
  return 'unknown';
}

/**
 * Keys that read as *interpretation* rather than *observation*.
 *
 * This is a **suspect list**, not a verdict. The Semantic Ceiling's real
 * enforcement lives in Vision OS's four gates; the console's job is to point an
 * engineer at anything worth a second look. Flagging is cheap; a false
 * accusation costs only a glance, while a missed violation ships.
 */
const CEILING_SUSPECTS = [
  'exceeded',
  'threshold',
  'violation_detected',
  'is_suspicious',
  'alert',
  'compliant',
  'risk',
  'score_grade',
  'should_',
  'recommend',
];

function auditCeiling(messages: Array<{ type: string; payload: unknown }>) {
  const violations: Array<{ observation_id?: string; key: string }> = [];
  let untraceable = 0;

  for (const message of messages) {
    if (message.type !== 'observation') continue;
    const observation = message.payload as Observation;
    if (!observation?.provenance) untraceable += 1;

    for (const attribute of observation?.attributes ?? []) {
      const key = String((attribute as Record<string, unknown>).key ?? '');
      const lowered = key.toLowerCase();
      if (CEILING_SUSPECTS.some((suspect) => lowered.includes(suspect))) {
        violations.push({ observation_id: observation.observation_id, key });
      }
    }
  }

  return { violations, untraceable };
}
