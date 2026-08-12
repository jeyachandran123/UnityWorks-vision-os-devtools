/**
 * Observations — the platform's actual output, delivered over the real M14
 * subscription.
 *
 * This is not a tap on an internal queue. The harness subscribes through
 * `ObservationApi.subscribe`, the same public streaming contract a Cognitive OS
 * integration will use, so what this panel shows is exactly what production will
 * receive — including its gaps.
 */

import { useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import type { GapPayload, Observation, TapMessage } from '@contract/types';
import { useChannel, useConsole } from '@state/ConsoleProvider';
import {
  ConfidenceCell,
  DataTable,
  DetailPane,
  Mono,
  PanelShell,
  Row,
  StatTile,
  Unavailable,
} from '@components/primitives';
import { ObservationLensView, type ObservationLens } from '@/narrative/NarrativeView';
import { semantic } from '@/theme/theme';

export function ObservationsPanel() {
  const { sessionId, gaps } = useConsole();
  const messages = useChannel('observation');
  const [selected, setSelected] = useState<TapMessage | null>(null);
  // Raw JSON is the default lens deliberately: prose is persuasive, and an
  // engineer should have to *choose* the readable view rather than land in it.
  const [lens, setLens] = useState<ObservationLens>('json');

  const { observations, streamGaps } = useMemo(() => {
    const obs: TapMessage[] = [];
    const gs: TapMessage[] = [];
    for (const message of messages) {
      if (message.type === 'gap') gs.push(message);
      else if (message.type === 'observation') obs.push(message);
    }
    return { observations: obs, streamGaps: gs };
  }, [messages]);

  const traceability = useMemo(() => {
    let withProvenance = 0;
    for (const message of observations) {
      const payload = message.payload as unknown as Observation;
      if (payload?.provenance) withProvenance += 1;
    }
    return { withProvenance, total: observations.length };
  }, [observations]);

  if (!sessionId) {
    return (
      <PanelShell title="Observations">
        <Unavailable what="Observations" reason="No session is open." />
      </PanelShell>
    );
  }

  const untraceable = traceability.total - traceability.withProvenance;

  return (
    <PanelShell
      title="Observations"
      subtitle={`${observations.length} delivered`}
      dense
    >
      <Stack sx={{ height: '100%', minHeight: 0 }}>
        <Box sx={{ px: 1, py: 0.5, borderBottom: '1px solid', borderColor: 'divider' }}>
          <Row gap={0.75}>
            <StatTile dense label="delivered" value={observations.length} />
            <StatTile
              dense
              label="with provenance"
              value={traceability.withProvenance}
              tone={untraceable === 0 ? 'good' : 'bad'}
              hint="V4: an observation without provenance is unexplainable, and §M11 calls that worse than no observation at all."
            />
            <StatTile
              dense
              label="stream gaps"
              value={streamGaps.length + gaps.length}
              tone={streamGaps.length + gaps.length > 0 ? 'warn' : 'good'}
              hint="Every drop produces an explicit gap. This count is never inferred from a silence."
            />
          </Row>
        </Box>

        {untraceable > 0 ? (
          <Alert severity="error" variant="outlined" sx={{ m: 1 }}>
            {untraceable} observation{untraceable === 1 ? '' : 's'} arrived without provenance. That
            is a <strong>V4 violation</strong> in Vision OS, not a console warning.
          </Alert>
        ) : null}

        {(streamGaps.length > 0 || gaps.length > 0) && <GapBanner remote={streamGaps} local={gaps} />}

        {/* `minHeight` keeps the list reachable when the detail pane is open —
            without it a tall inspector collapses the rows it was opened from. */}
        <Box sx={{ flex: 1, minHeight: 120, overflow: 'auto' }}>
          <DataTable
            rows={observations.slice(-600).reverse()}
            columns={[
              {
                key: 'type',
                header: 'type',
                width: 150,
                render: (row) => <Mono>{String(obs(row).observation_type ?? '—')}</Mono>,
              },
              {
                key: 'object',
                header: 'object',
                width: 200,
                render: (row) => <Mono>{String(obs(row).object_id ?? '—')}</Mono>,
              },
              {
                key: 'class',
                header: 'class',
                width: 110,
                render: (row) => <Mono>{String(obs(row).class_id ?? '—')}</Mono>,
              },
              {
                key: 'confidence',
                header: 'confidence',
                width: 100,
                render: (row) => <ConfidenceCell confidence={obs(row).confidence as never} />,
              },
              {
                key: 'provenance',
                header: 'traceable',
                width: 90,
                render: (row) =>
                  obs(row).provenance ? (
                    <Mono colour={semantic.observing}>yes</Mono>
                  ) : (
                    <Mono colour={semantic.blind}>NO</Mono>
                  ),
              },
              {
                key: 'supersedes',
                header: 'supersedes',
                render: (row) => <Mono>{String(obs(row).supersedes ?? '—')}</Mono>,
              },
            ]}
            emptyWhat="observations delivered yet"
            emptyNote="Observations are suppressed when nothing changed — a 10–50× reduction is the designed behaviour, not a fault."
            onRowClick={setSelected}
            rowKey={(row) => String(row.seq)}
            selectedKey={selected ? String(selected.seq) : undefined}
          />
        </Box>

        {selected ? (
          <DetailPane
            title="Observation"
            subtitle={String(obs(selected).observation_id ?? '')}
            onClose={() => setSelected(null)}
            storageKey="observations"
            actions={
              <ToggleButtonGroup
                size="small"
                exclusive
                value={lens}
                onChange={(_, value: ObservationLens | null) => value && setLens(value)}
              >
                <ToggleButton value="json">Raw JSON</ToggleButton>
                <ToggleButton value="structured">Structured</ToggleButton>
                <ToggleButton value="narrative">Narrative</ToggleButton>
              </ToggleButtonGroup>
            }
          >
            <Typography variant="caption" sx={{ display: 'block', mb: 1 }}>
              The same record, three ways. If they ever disagree, the JSON is right.
            </Typography>
            <ObservationLensView
              observation={selected.payload as unknown as Observation}
              lens={lens}
            />
          </DetailPane>
        ) : null}
      </Stack>
    </PanelShell>
  );
}

function obs(message: TapMessage): Observation {
  return message.payload as unknown as Observation;
}

/**
 * Gaps are rendered as a stated absence with a backfill offer where the platform
 * says one is possible — and no offer where it is not.
 *
 * > *"An empty result over a window with observable_fraction < 1.0 does not mean
 * > nothing happened. It means nothing was observed."*
 */
function GapBanner({
  remote,
  local,
}: {
  remote: TapMessage[];
  local: Array<GapPayload & { local: boolean }>;
}) {
  const all = [
    ...remote.map((m) => m.payload as unknown as GapPayload),
    ...local,
  ];
  const recoverable = all.filter((g) => g.recoverable).length;
  const permanent = all.length - recoverable;

  return (
    <Alert
      severity="warning"
      variant="outlined"
      sx={{ m: 1, borderColor: semantic.gap, color: 'text.primary' }}
      action={
        recoverable > 0 ? (
          <Button size="small" color="inherit" href="#/observations-query">
            Backfill
          </Button>
        ) : undefined
      }
    >
      <strong>{all.length} gap{all.length === 1 ? '' : 's'}.</strong>{' '}
      {recoverable} recoverable by querying the window; {permanent} not — for those the platform was
      blind and there is nothing to fetch.
      <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
        Reasons: {Array.from(new Set(all.map((g) => g.reason))).join(', ')}
      </Typography>
    </Alert>
  );
}
