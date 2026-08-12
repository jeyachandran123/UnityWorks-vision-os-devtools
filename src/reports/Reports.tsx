/**
 * The eight reports.
 *
 * A report **restates recorded facts**. It computes no new ones, invents no
 * score, and assigns no grade. The regression report is the one that most
 * tempts a verdict and deliberately withholds it: it says "session B produced 14
 * observations session A did not, here they are", never "session B is worse".
 * Which is better requires ground truth the platform does not have — V1 binds
 * the tool that validates the platform just as it binds the platform.
 *
 * Every report is exportable as JSON. The export is the artefact that goes into
 * a release record, so it contains the raw harness payload rather than the
 * console's rendering of it.
 */

import { useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Divider,
  MenuItem,
  Paper,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import type { ReportKind } from '@contract/types';
import { useConsole } from '@state/ConsoleProvider';
import { useReport, useSessions } from '@state/queries';
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

const KINDS: Array<{ kind: ReportKind; label: string; blurb: string }> = [
  { kind: 'summary', label: 'Validation Summary', blurb: 'The eight verification checks, each with its evidence.' },
  { kind: 'replay', label: 'Replay', blurb: 'Transport history, frame ledger, determinism verdict.' },
  { kind: 'performance', label: 'Performance', blurb: 'Metrics Engine series over the session window.' },
  { kind: 'observation', label: 'Observation', blurb: 'Every observation, with provenance and evidence refs.' },
  { kind: 'architecture', label: 'Architecture', blurb: 'Health, layers, channels observed, partitions.' },
  { kind: 'failure', label: 'Failure', blurb: 'Armed scenarios, expected response, observed response.' },
  { kind: 'latency', label: 'Latency', blurb: 'Per-module execution time, from the platform histograms.' },
  { kind: 'regression', label: 'Regression', blurb: 'A diff against a baseline session. A diff, not a judgment.' },
];

export function Reports() {
  const { sessionId } = useConsole();
  const [tab, setTab] = useState(0);
  const [baseline, setBaseline] = useState('');
  const active = KINDS[tab]!;
  const { data, error, isLoading } = useReport(active.kind, baseline || undefined);
  const { data: sessions } = useSessions();

  const download = () => {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `vosvc-${active.kind}-${sessionId ?? 'none'}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if (!sessionId) {
    return (
      <PanelShell title="Reports">
        <Unavailable what="Reports" reason="No session is open." />
      </PanelShell>
    );
  }

  return (
    <PanelShell
      title="Reports"
      subtitle={active.blurb}
      actions={
        <Button size="small" startIcon={<DownloadIcon />} onClick={download} disabled={!data}>
          Export JSON
        </Button>
      }
      dense
    >
      <Stack sx={{ height: '100%', minHeight: 0 }}>
        <Tabs
          value={tab}
          onChange={(_, value) => setTab(value)}
          variant="scrollable"
          scrollButtons="auto"
          sx={{ borderBottom: '1px solid', borderColor: 'divider', flexShrink: 0 }}
        >
          {KINDS.map((entry) => (
            <Tab key={entry.kind} label={entry.label} />
          ))}
        </Tabs>

        <Box sx={{ flex: 1, minHeight: 0, overflow: 'auto', p: 1.25 }}>
          {active.kind === 'regression' ? (
            <TextField
              select
              size="small"
              label="baseline session"
              value={baseline}
              onChange={(event) => setBaseline(event.target.value)}
              sx={{ width: 320, mb: 1.5 }}
              helperText="A comparison across different media or different source semantics is meaningless and will be refused."
            >
              <MenuItem value="">— none —</MenuItem>
              {(sessions?.sessions ?? [])
                .filter((s) => s.session_id !== sessionId)
                .map((s) => (
                  <MenuItem key={s.session_id} value={s.session_id}>
                    {s.session_id} · {s.media_name} · {s.semantics}
                  </MenuItem>
                ))}
            </TextField>
          ) : null}

          {isLoading ? (
            <EmptyResult what="report yet" note="Generating…" />
          ) : error ? (
            <Unavailable what={active.label} reason={String(error)} />
          ) : !data?.available ? (
            <Unavailable what={active.label} reason={String(data?.reason ?? 'unavailable')} />
          ) : (
            <ReportBody kind={active.kind} data={data} />
          )}
        </Box>
      </Stack>
    </PanelShell>
  );
}

function ReportBody({ kind, data }: { kind: ReportKind; data: Record<string, unknown> }) {
  if (kind === 'summary') return <SummaryReport data={data} />;
  if (kind === 'regression') return <RegressionReport data={data} />;
  if (kind === 'failure') return <FailureReport data={data} />;
  if (kind === 'replay') return <ReplayReport data={data} />;
  return <JsonView value={data} maxHeight="100%" />;
}

function SummaryReport({ data }: { data: Record<string, unknown> }) {
  const checks = (data.checks ?? []) as Array<{ check: string; state: string; evidence: unknown }>;
  const verdict = String(data.verdict ?? 'unknown');

  return (
    <Stack spacing={1.5}>
      <Paper elevation={0} sx={{ p: 1.5 }}>
        <Stack direction="row" spacing={2} alignItems="center">
          <Typography variant="h2">Validation verdict</Typography>
          <VerdictChip verdict={verdict} />
        </Stack>
        <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
          Every check carries the evidence it was decided on. A check that could not be measured
          reports <Mono>indeterminate</Mono> — never a pass by default.
        </Typography>
      </Paper>

      <DataTable
        rows={checks}
        columns={[
          { key: 'check', header: 'check', width: 320, render: (c) => <Mono>{c.check}</Mono> },
          { key: 'state', header: 'state', width: 130, render: (c) => <VerdictChip verdict={c.state} /> },
          {
            key: 'evidence',
            header: 'evidence',
            render: (c) => (
              <Mono colour={semantic.unknown}>
                {typeof c.evidence === 'string' ? c.evidence : JSON.stringify(c.evidence)}
              </Mono>
            ),
          },
        ]}
        emptyWhat="checks"
        rowKey={(c) => c.check}
      />

      {Array.isArray(data.unvalidated_scenarios) && data.unvalidated_scenarios.length > 0 ? (
        <Alert severity="error" variant="outlined">
          Unvalidated failure scenarios remain. The summary will not report
          <Mono> suitable-for-validation</Mono> while any scenario's expected architectural response
          is missing.
        </Alert>
      ) : null}
    </Stack>
  );
}

function RegressionReport({ data }: { data: Record<string, unknown> }) {
  const comparable = Boolean(data.comparable);
  const counts = (data.counts ?? {}) as { baseline?: number; current?: number };
  const onlyCurrent = (data.only_in_current ?? []) as string[];
  const onlyBaseline = (data.only_in_baseline ?? []) as string[];

  if (!comparable) {
    return (
      <Unavailable
        what="Regression comparison"
        reason={String(data.incomparable_reason ?? data.reason ?? 'not comparable')}
      />
    );
  }

  return (
    <Stack spacing={1.5}>
      <Row>
        <StatTile label="baseline" value={counts.baseline ?? 0} />
        <StatTile label="current" value={counts.current ?? 0} />
        <StatTile label="common" value={Number(data.common ?? 0)} />
        <StatTile label="only in current" value={onlyCurrent.length} tone={onlyCurrent.length ? 'warn' : 'good'} />
        <StatTile label="only in baseline" value={onlyBaseline.length} tone={onlyBaseline.length ? 'warn' : 'good'} />
      </Row>

      <Alert severity="info" variant="outlined">
        {String(data.note ?? '')}
      </Alert>

      <Divider />
      <Typography variant="h3">Only in current</Typography>
      <JsonView value={onlyCurrent.slice(0, 200)} maxHeight={200} />
      <Typography variant="h3">Only in baseline</Typography>
      <JsonView value={onlyBaseline.slice(0, 200)} maxHeight={200} />
    </Stack>
  );
}

function FailureReport({ data }: { data: Record<string, unknown> }) {
  const armed = (data.armed ?? []) as Array<Record<string, unknown>>;
  const unvalidated = (data.unvalidated ?? []) as Array<Record<string, unknown>>;

  return (
    <Stack spacing={1.5}>
      <Row>
        <StatTile label="armed" value={armed.length} />
        <StatTile
          label="unvalidated"
          value={unvalidated.length}
          tone={unvalidated.length ? 'bad' : 'good'}
        />
        <StatTile
          label="observed event types"
          value={((data.observed_event_types ?? []) as string[]).length}
        />
      </Row>
      <Alert severity="info" variant="outlined">
        {String(data.note ?? '')}
      </Alert>
      <JsonView value={armed} maxHeight={320} />
      <Typography variant="h3">Expected response per scenario</Typography>
      <JsonView value={data.expectations} maxHeight={220} />
    </Stack>
  );
}

function ReplayReport({ data }: { data: Record<string, unknown> }) {
  const determinism = (data.determinism ?? {}) as { mismatches?: number; verdict?: string };
  const transport = (data.transport_history ?? []) as Array<Record<string, unknown>>;

  const ledger = useMemo(
    () => ((data.frame_ledger ?? []) as Array<Record<string, unknown>>).slice(0, 500),
    [data],
  );

  return (
    <Stack spacing={1.5}>
      <Row>
        <StatTile label="frames" value={Number(data.frame_count ?? 0)} />
        <StatTile label="emitted" value={Number(data.frames_emitted ?? 0)} />
        <StatTile label="semantics" value={String(data.semantics ?? '—')} />
        <StatTile label="target fps" value={Number(data.target_fps ?? 0)} />
        <StatTile
          label="determinism"
          value={determinism.verdict ?? '—'}
          tone={determinism.mismatches === 0 ? 'good' : 'bad'}
        />
      </Row>

      {determinism.mismatches !== 0 ? (
        <Alert severity="error" variant="outlined">
          <strong>Replay mismatch.</strong> A rebuild produced a different world from the live run.
          This invalidates every recovery guarantee in 07_STATE §9.1 and is a Vision OS failure, not
          a console warning.
        </Alert>
      ) : null}

      <Typography variant="h3">Transport history</Typography>
      <DataTable
        rows={transport}
        columns={[
          { key: 'action', header: 'action', width: 160, render: (r) => <Mono>{String(r.action)}</Mono> },
          { key: 'frame', header: 'frame', width: 90, render: (r) => <Mono>{String(r.frame_index)}</Mono> },
          {
            key: 'detail',
            header: 'detail',
            render: (r) => (
              <Mono colour={semantic.unknown}>
                {JSON.stringify(
                  Object.fromEntries(
                    Object.entries(r).filter(([k]) => !['action', 'frame_index', 'at_ns'].includes(k)),
                  ),
                )}
              </Mono>
            ),
          },
        ]}
        emptyWhat="transport commands"
        rowKey={(_, index) => String(index)}
      />

      <Typography variant="h3">Frame ledger (first 500)</Typography>
      <JsonView value={ledger} maxHeight={240} />
    </Stack>
  );
}
