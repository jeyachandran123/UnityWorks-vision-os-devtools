/**
 * Demand Registry — the only inbound path, and the only place this console
 * changes anything.
 *
 * > §M14: *"Influence, not a write: a demand changes what the platform chooses
 * > to compute and cannot change any published fact."*
 *
 * The panel says so on screen, because an engineer registering a demand should
 * know they are spending compute budget rather than editing state. 12_SECURITY
 * §5.3 is explicit that this is not a read: *"Demands spend money and cause
 * computation."*
 */

import { useState } from 'react';
import { Alert, Button, Chip, Stack, TextField, Typography } from '@mui/material';
import type { DemandView } from '@contract/types';
import { useConsole } from '@state/ConsoleProvider';
import { useDemands } from '@state/queries';
import {
  DataTable,
  Mono,
  PanelShell,
  Row,
  StatTile,
  Unavailable,
} from '@components/primitives';
import { describeError } from '@transport/errors';
import { semantic } from '@/theme/theme';

export function DemandRegistryPanel() {
  const { client, sessionId } = useConsole();
  const { data, error, refetch } = useDemands();
  const [attribute, setAttribute] = useState('');
  const [classId, setClassId] = useState('person');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  if (!sessionId) {
    return (
      <PanelShell title="Demand Registry">
        <Unavailable what="Demand Registry" reason="No session is open." />
      </PanelShell>
    );
  }

  if (error) {
    const described = describeError(error);
    return (
      <PanelShell title="Demand Registry">
        <Unavailable what="Demand Registry" reason={`${described.code}: ${described.message}`} />
      </PanelShell>
    );
  }

  if (data?.unavailable) {
    return (
      <PanelShell title="Demand Registry">
        <Unavailable what="Demand Registry" reason={data.unavailable} />
      </PanelShell>
    );
  }

  const demands = data?.demands ?? [];
  const unsatisfiable = demands.filter((d) => d.unsatisfiable.length > 0);

  const register = async () => {
    setBusy(true);
    setFailure(null);
    try {
      await client.registerDemand({
        session_id: sessionId,
        class_id: classId.trim() || 'person',
        required_attributes: attribute
          .split(',')
          .map((a) => a.trim())
          .filter(Boolean),
      });
      setAttribute('');
      await refetch();
    } catch (registerError) {
      const described = describeError(registerError);
      setFailure(`${described.code}: ${described.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <PanelShell title="Demand Registry" subtitle={`${demands.length} registered`}>
      <Stack spacing={1.25}>
        <Alert severity="info" variant="outlined">
          A demand is <strong>influence, not a write</strong>. It changes what the platform chooses
          to compute; it cannot change any published fact. It also spends budget — 12_SECURITY §5.3
          classifies registering one as <em>not</em> a read.
        </Alert>

        <Row>
          <StatTile label="registered" value={demands.length} />
          <StatTile
            label="unsatisfiable"
            value={unsatisfiable.length}
            tone={unsatisfiable.length ? 'warn' : 'good'}
            hint="Demands the platform accepted and cannot serve — the honest answer to a request for an attribute no bound model produces."
          />
        </Row>

        <Stack direction="row" spacing={1} alignItems="flex-start">
          <TextField
            size="small"
            label="class_id"
            value={classId}
            onChange={(event) => setClassId(event.target.value)}
            sx={{ width: 140 }}
          />
          <TextField
            size="small"
            label="required attributes (comma separated)"
            value={attribute}
            onChange={(event) => setAttribute(event.target.value)}
            sx={{ flex: 1 }}
          />
          <Button variant="outlined" disabled={busy} onClick={register}>
            Register
          </Button>
        </Stack>

        {failure ? (
          <Alert severity="error" variant="outlined">
            {failure}
          </Alert>
        ) : null}

        <DataTable
          rows={demands}
          columns={[
            { key: 'id', header: 'demand id', width: 200, render: (d) => <Mono>{d.demand_id}</Mono> },
            { key: 'subscriber', header: 'subscriber', width: 140, render: (d) => <Mono>{d.subscriber}</Mono> },
            {
              key: 'status',
              header: 'status',
              width: 110,
              render: (d) => <Chip size="small" label={d.status} />,
            },
            {
              key: 'attrs',
              header: 'required attributes',
              render: (d) => <Mono>{d.required_attributes.join(', ') || '—'}</Mono>,
            },
            {
              key: 'unsat',
              header: 'unsatisfiable',
              render: (d: DemandView) =>
                d.unsatisfiable.length ? (
                  <Mono colour={semantic.degraded}>
                    {d.unsatisfiable.map(([k, why]) => `${k}: ${why}`).join('; ')}
                  </Mono>
                ) : (
                  <Mono colour={semantic.unknown}>—</Mono>
                ),
            },
            {
              key: 'revoke',
              header: '',
              width: 80,
              render: (d) => (
                <Button
                  size="small"
                  onClick={async () => {
                    await client.revokeDemand(d.demand_id, sessionId);
                    await refetch();
                  }}
                >
                  revoke
                </Button>
              ),
            },
          ]}
          emptyWhat="demands registered"
          emptyNote="With no demands, the Crop Manager has nothing to prioritise and understanding runs on cadence alone."
          rowKey={(d) => d.demand_id}
        />

        <Typography variant="caption">
          The Crop Manager reads this registry; the Observation API writes it. Neither calls the
          other — 01_LAYERED §3.2 breaks the cycle by having both touch one record store.
        </Typography>
      </Stack>
    </PanelShell>
  );
}
