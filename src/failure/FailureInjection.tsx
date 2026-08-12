/**
 * Failure injection.
 *
 * Eleven scenarios, each injected at the P1 source or P2 decoder inside the
 * harness — never by reaching into a Vision OS module. That restraint is the
 * whole value: the platform experiences a genuinely bad camera and answers with
 * its real degradation ladder.
 *
 * The verdict vocabulary has four values and no fifth:
 *
 * - `validated` — every expected event arrived **after** arming.
 * - `unvalidated` — one did not. Not a pass, and never upgraded to one.
 * - `not_reached` — nothing was injected, so nothing was tested.
 * - `observational` — the scenario mandates no event; read the quality
 *   distribution instead. The Semantic Ceiling forbids the platform concluding
 *   "it is raining", so demanding an event for `rain` would be demanding a V1
 *   violation.
 */

import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import type { FaultVerdict, ScenarioName } from '@contract/types';
import { SCENARIOS } from '@contract/types';
import { useConsole } from '@state/ConsoleProvider';
import { useArmFault, useClearFaults, useFaults } from '@state/queries';
import {
  DataTable,
  Mono,
  PanelShell,
  Row,
  StatTile,
  Unavailable,
  VerdictChip,
} from '@components/primitives';
import { describeError } from '@transport/errors';
import { mono, semantic } from '@/theme/theme';

const DESCRIPTIONS: Record<ScenarioName, { stage: string; how: string; expect: string }> = {
  blur: { stage: 'P2 decoder', how: 'Separable box blur on the decoded plane', expect: 'Crop quality grades fall; the gate may reject. No event is mandated — a threshold is configuration.' },
  low_light: { stage: 'P2 decoder', how: 'Multiplicative darkening; DecodeOutcome.exposure = "under"', expect: 'Quality grading reflects exposure. No verdict about lighting — that would be interpretation.' },
  rain: { stage: 'P2 decoder', how: 'Deterministic bright streaks, phase-advanced per frame', expect: 'Deterministic so replay reproduces it — a stochastic fault would make V13 untestable exactly when you want it.' },
  occlusion: { stage: 'P2 decoder', how: 'Opaque band over a fraction of the frame', expect: 'Detections drop; tracks coast then terminate with a break_reason.' },
  camera_disconnect: { stage: 'P1 source', how: 'Raises StreamLostError mid-stream', expect: 'stream.lost → stream.epoch_advanced → coverage change. Tracks are discarded; object identity survives.' },
  duplicate_frames: { stage: 'P1 source', how: 'Re-emits the prior packet', expect: 'Change detection and dedup absorb it. No event is mandated.' },
  dropped_frames: { stage: 'P1 source', how: 'Skips every Nth packet', expect: 'Coverage degrades and says so — absence of observation is never observation of absence.' },
  freeze: { stage: 'P1 source', how: 'Re-emits an identical payload while advancing PTS', expect: 'health.silent_failure_suspected — a suspicion, never an automatic blinding.' },
  slow_camera: { stage: 'P1 source', how: 'Inflates inter-packet delay on the platform clock', expect: 'scheduler.sustained_drop once shedding exceeds tolerance.' },
  restart: { stage: 'session', how: 'Tears the pipeline down and re-attaches it', expect: 'pipeline_detached → pipeline_attached. Then record the restart window as a coverage observation.' },
  network_delay: { stage: 'P1 source', how: 'Delays delivery while preserving order', expect: 'Ingest latency rises. Order is preserved, so no out-of-order alarm should fire.' },
};

export function FailureInjection() {
  const { sessionId } = useConsole();
  const { data, error, refetch } = useFaults();
  const arm = useArmFault();
  const clear = useClearFaults();

  const [scenario, setScenario] = useState<ScenarioName>('occlusion');
  const [atFrame, setAtFrame] = useState('');
  const [duration, setDuration] = useState('');
  const [param, setParam] = useState('');

  if (!sessionId) {
    return (
      <PanelShell title="Failure Injection">
        <Unavailable what="Failure injection" reason="No session is open." />
      </PanelShell>
    );
  }

  if (error) {
    const described = describeError(error);
    return (
      <PanelShell title="Failure Injection">
        <Unavailable what="Failure injection" reason={`${described.code}: ${described.message}`} />
      </PanelShell>
    );
  }

  const armed: FaultVerdict[] = data?.armed ?? [];
  const unvalidated = armed.filter((v) => v.verdict === 'unvalidated');
  const notReached = armed.filter((v) => v.verdict === 'not_reached');
  const description = DESCRIPTIONS[scenario];

  const submit = () => {
    const params: Record<string, number> = {};
    if (param.trim()) {
      for (const pair of param.split(',')) {
        const [key, value] = pair.split('=').map((s) => s.trim());
        if (key && value && Number.isFinite(Number(value))) params[key] = Number(value);
      }
    }
    arm.mutate(
      {
        scenario,
        ...(atFrame ? { at_frame: Number(atFrame) } : {}),
        ...(duration ? { duration_frames: Number(duration) } : {}),
        ...(Object.keys(params).length ? { params } : {}),
      },
      { onSuccess: () => refetch() },
    );
  };

  return (
    <PanelShell
      title="Failure Injection"
      subtitle={`${armed.length} armed`}
      actions={
        <Button size="small" onClick={() => clear.mutate(undefined, { onSuccess: () => refetch() })}>
          clear all
        </Button>
      }
    >
      <Stack spacing={1.5}>
        <Row>
          <StatTile label="armed" value={armed.length} />
          <StatTile
            label="unvalidated"
            value={unvalidated.length}
            tone={unvalidated.length ? 'bad' : 'good'}
            hint="The expected architectural response did not arrive. This is never upgraded to a pass."
          />
          <StatTile
            label="not reached"
            value={notReached.length}
            tone={notReached.length ? 'warn' : 'default'}
            hint="Armed but never injected — the replay has not reached the frame, or is paused."
          />
        </Row>

        {unvalidated.length > 0 ? (
          <Alert severity="error" variant="outlined">
            {unvalidated.length} scenario{unvalidated.length === 1 ? '' : 's'} did not produce the
            expected response:{' '}
            <Mono>{unvalidated.map((v) => `${v.scenario} (missing ${v.missing_events.join(', ')})`).join('; ')}</Mono>
          </Alert>
        ) : null}

        <Paper elevation={0} sx={{ p: 1.25 }}>
          <Stack direction="row" spacing={1} alignItems="flex-start" flexWrap="wrap" useFlexGap>
            <TextField
              select
              size="small"
              label="scenario"
              value={scenario}
              onChange={(event) => setScenario(event.target.value as ScenarioName)}
              sx={{ width: 200 }}
            >
              {SCENARIOS.map((name) => (
                <MenuItem key={name} value={name}>
                  {name}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              label="at frame"
              value={atFrame}
              onChange={(event) => setAtFrame(event.target.value)}
              placeholder="now"
              sx={{ width: 110 }}
            />
            <TextField
              size="small"
              label="duration (frames)"
              value={duration}
              onChange={(event) => setDuration(event.target.value)}
              placeholder="until cleared"
              sx={{ width: 150 }}
            />
            <TextField
              size="small"
              label="params (k=v, k=v)"
              value={param}
              onChange={(event) => setParam(event.target.value)}
              placeholder="coverage=0.4"
              sx={{ flex: 1, minWidth: 180 }}
            />
            <Button variant="contained" onClick={submit} disabled={arm.isPending}>
              Arm
            </Button>
          </Stack>

          <Divider sx={{ my: 1.25 }} />
          <Stack spacing={0.5}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Chip size="small" label={description.stage} sx={{ color: semantic.injected, border: `1px solid ${semantic.injected}55` }} />
              <Typography variant="caption" sx={{ fontFamily: mono }}>
                {description.how}
              </Typography>
            </Stack>
            <Typography variant="caption">
              <strong>Expected:</strong> {description.expect}
            </Typography>
          </Stack>
        </Paper>

        <DataTable
          rows={armed}
          columns={[
            { key: 'scenario', header: 'scenario', width: 150, render: (v) => <Mono>{v.scenario}</Mono> },
            { key: 'stage', header: 'stage', width: 90, render: (v) => <Mono>{v.stage}</Mono> },
            {
              key: 'window',
              header: 'window',
              width: 130,
              render: (v) => (
                <Mono>
                  {v.at_frame}
                  {v.duration_frames ? `–${v.at_frame + v.duration_frames}` : '+'}
                </Mono>
              ),
            },
            {
              key: 'injections',
              header: 'injected',
              width: 90,
              render: (v) => (
                <Mono colour={v.injections.length ? semantic.injected : semantic.unknown}>
                  {v.injections.length}
                </Mono>
              ),
            },
            {
              key: 'expected',
              header: 'expected events',
              render: (v) =>
                v.expected_events.length ? (
                  <Mono>{v.expected_events.join(', ')}</Mono>
                ) : (
                  <Tooltip title="No event is mandated. The Semantic Ceiling forbids the platform from concluding what the degradation means; read the crop-quality distribution instead." arrow>
                    <span>
                      <Mono colour={semantic.unknown}>observational</Mono>
                    </span>
                  </Tooltip>
                ),
            },
            {
              key: 'missing',
              header: 'missing',
              render: (v) =>
                v.missing_events.length ? (
                  <Mono colour={semantic.blind}>{v.missing_events.join(', ')}</Mono>
                ) : (
                  <Mono colour={semantic.unknown}>—</Mono>
                ),
            },
            {
              key: 'verdict',
              header: 'verdict',
              width: 120,
              render: (v) => <VerdictChip verdict={v.verdict} />,
            },
            {
              key: 'clear',
              header: '',
              width: 70,
              render: (v) => (
                <Button
                  size="small"
                  onClick={() =>
                    clear.mutate(v.scenario as ScenarioName, { onSuccess: () => refetch() })
                  }
                >
                  clear
                </Button>
              ),
            },
          ]}
          emptyWhat="scenarios armed"
          emptyNote="Arm one above. Faults are injected at the port boundary, so the platform's response is its real one."
          rowKey={(v) => v.scenario}
        />

        <Box>
          <Typography variant="caption">
            Every scenario is injected inside the harness's own P1/P2 adapter. Nothing reaches into a
            Vision OS module — a fault poked into a module would be testing the poke.
          </Typography>
        </Box>
      </Stack>
    </PanelShell>
  );
}
