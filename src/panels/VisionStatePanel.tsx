/**
 * Vision State — the projection, queried through the real Observation API.
 *
 * The state inspector. Object lifecycle, attributes with their evidence refs,
 * provenance, confidence and coverage, all as the platform returned them.
 *
 * **Coverage is rendered unconditionally.** It accompanies every state answer in
 * the platform and it accompanies every state view here; a result that arrived
 * without one is shown as a contract violation rather than silently omitted.
 */

import { useState } from 'react';
import { Alert, Box, Chip, Divider, Stack, Typography } from '@mui/material';
import type { ObjectView } from '@contract/types';
import { useConsole } from '@state/ConsoleProvider';
import { useVisionState } from '@state/queries';
import {
  ConfidenceCell,
  CoverageBadge,
  DataTable,
  DetailPane,
  EmptyResult,
  JsonView,
  Mono,
  PanelShell,
  Row,
  StatTile,
  Unavailable,
} from '@components/primitives';
import { describeError } from '@transport/errors';
import { semantic } from '@/theme/theme';

const LIFECYCLE_TONE: Record<string, string> = {
  provisional: semantic.degraded,
  active: semantic.observing,
  occluded: semantic.degraded,
  lost: semantic.blind,
  expired: semantic.unknown,
  merged_into: semantic.unknown,
};

export function VisionStatePanel() {
  const { sessionId } = useConsole();
  const { data, error, isLoading } = useVisionState();
  const [selected, setSelected] = useState<ObjectView | null>(null);

  if (!sessionId) {
    return (
      <PanelShell title="Vision State">
        <Unavailable what="Vision State" reason="No session is open." />
      </PanelShell>
    );
  }

  if (error) {
    const described = describeError(error);
    return (
      <PanelShell title="Vision State">
        <Unavailable what="Vision State" reason={`${described.code}: ${described.message}`} />
      </PanelShell>
    );
  }

  const objects = data?.objects ?? [];

  return (
    <PanelShell
      title="Vision State"
      subtitle={`${objects.length} objects`}
      actions={<CoverageBadge coverage={data?.coverage} />}
      dense
    >
      <Stack sx={{ height: '100%', minHeight: 0 }}>
        <Box sx={{ p: 1, borderBottom: '1px solid', borderColor: 'divider' }}>
          <Row>
            <StatTile label="objects" value={objects.length} />
            <StatTile
              label="partitions"
              value={data?.snapshot.partitions.length ?? 0}
              hint="State is partitioned by camera; a snapshot never spans tenants."
            />
            <StatTile label="consistency" value={data?.snapshot.consistency ?? '—'} />
            <StatTile label="max lag" value={data?.snapshot.max_lag_ms?.toFixed(1) ?? '—'} unit="ms" />
            <StatTile
              label="complete"
              value={data?.complete ? 'yes' : 'no'}
              tone={data?.complete ? 'good' : 'warn'}
              hint="A consumer concluding 'nothing is here' must check this and coverage first."
            />
          </Row>
        </Box>

        {data && !data.complete ? (
          <Alert severity="warning" variant="outlined" sx={{ m: 1 }}>
            This result is <strong>partial</strong>. Some partition did not answer, so an empty or
            thin result here does not mean the scene was empty.
            {data.snapshot.incomplete.length > 0 && (
              <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
                Incomplete: {data.snapshot.incomplete.map(([c, r]) => `${c} (${r})`).join(', ')}
              </Typography>
            )}
          </Alert>
        ) : null}

        {/* `minHeight` keeps the list reachable when the detail pane is open. */}
        <Box sx={{ flex: 1, minHeight: 120, overflow: 'auto' }}>
          {isLoading && objects.length === 0 ? (
            <EmptyResult what="objects yet" note="Querying the projection…" />
          ) : (
            <DataTable
              rows={objects}
              columns={[
                {
                  key: 'object_id',
                  header: 'object id',
                  width: 210,
                  render: (row) => <Mono>{row.object_id}</Mono>,
                },
                { key: 'class', header: 'class', width: 110, render: (row) => <Mono>{row.class_id}</Mono> },
                {
                  key: 'lifecycle',
                  header: 'lifecycle',
                  width: 110,
                  render: (row) => (
                    <Chip
                      size="small"
                      label={row.lifecycle}
                      sx={{
                        bgcolor: 'transparent',
                        color: LIFECYCLE_TONE[row.lifecycle] ?? semantic.unknown,
                        border: `1px solid ${LIFECYCLE_TONE[row.lifecycle] ?? semantic.unknown}55`,
                      }}
                    />
                  ),
                },
                {
                  key: 'confidence',
                  header: 'class conf',
                  width: 100,
                  render: (row) => <ConfidenceCell confidence={row.class_confidence} />,
                },
                {
                  key: 'stale',
                  header: 'stale',
                  width: 70,
                  render: (row) =>
                    row.is_stale ? (
                      <Mono colour={semantic.degraded}>yes</Mono>
                    ) : (
                      <Mono colour={semantic.unknown}>no</Mono>
                    ),
                },
                {
                  key: 'attributes',
                  header: 'attrs',
                  width: 70,
                  render: (row) => <Mono>{Object.keys(row.attributes ?? {}).length}</Mono>,
                },
                {
                  key: 'observations',
                  header: 'obs',
                  width: 70,
                  render: (row) => <Mono>{row.observation_count}</Mono>,
                },
              ]}
              emptyWhat="objects in state"
              emptyNote="Check coverage above before concluding the scene was empty."
              onRowClick={setSelected}
              rowKey={(row) => row.object_id}
              selectedKey={selected?.object_id}
            />
          )}
        </Box>

        {selected ? (
          <DetailPane
            title="Object"
            subtitle={selected.object_id}
            onClose={() => setSelected(null)}
            storageKey="vision-state"
          >
            <ObjectDetail object={selected} />
          </DetailPane>
        ) : null}
      </Stack>
    </PanelShell>
  );
}

function ObjectDetail({ object }: { object: ObjectView }) {
  const attributes = Object.values(object.attributes ?? {});
  return (
    <Box>
      <Typography variant="h3" sx={{ mb: 0.75 }}>
        Object lifecycle
      </Typography>
      <Row>
        <StatTile label="first seen" value={`${(object.first_seen_ns / 1e6).toFixed(1)}`} unit="ms" />
        <StatTile label="last seen" value={`${(object.last_seen_ns / 1e6).toFixed(1)}`} unit="ms" />
        <StatTile
          label="last confirmed"
          value={`${(object.last_confirmed_ns / 1e6).toFixed(1)}`}
          unit="ms"
          tone={object.is_stale ? 'warn' : 'default'}
          hint="Time since the last MEASURED sighting. When this trails last_seen, the position is believed rather than measured."
        />
        <StatTile label="observations" value={object.observation_count} />
      </Row>

      <Divider sx={{ my: 1 }} />
      <Typography variant="h3" sx={{ mb: 0.5 }}>
        Attributes &amp; evidence
      </Typography>
      {attributes.length === 0 ? (
        <EmptyResult
          what="attributes"
          note="Understanding is triggered, demand-filtered, quality-gated and deduplicated — no attributes is a normal state, not a failure."
        />
      ) : (
        <DataTable
          rows={attributes}
          columns={[
            { key: 'key', header: 'key', width: 160, render: (a) => <Mono>{a.key}</Mono> },
            { key: 'value', header: 'value', render: (a) => <Mono>{JSON.stringify(a.value)}</Mono> },
            {
              key: 'confidence',
              header: 'confidence',
              width: 100,
              render: (a) => <ConfidenceCell confidence={a.confidence} />,
            },
            {
              key: 'evidence',
              header: 'evidence ref',
              render: (a) =>
                a.evidence_ref ? (
                  <Mono colour={semantic.injected}>{a.evidence_ref}</Mono>
                ) : (
                  <Mono colour={semantic.unknown}>none</Mono>
                ),
            },
          ]}
          emptyWhat="attributes"
          rowKey={(a) => a.key}
        />
      )}

      <Divider sx={{ my: 1 }} />
      <Typography variant="h3" sx={{ mb: 0.5 }}>
        Provenance &amp; spatial
      </Typography>
      <JsonView value={{ provenance: object.provenance, spatial: object.spatial, trajectory: object.trajectory }} maxHeight={160} />
    </Box>
  );
}
