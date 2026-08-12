/**
 * The generic layer inspector.
 *
 * Seven of the fourteen panels are the same thing pointed at a different
 * channel: a chronological list of what one Vision OS layer emitted, with the
 * raw payload one click away. Writing them seven times would mean seven places
 * to forget the unavailability case.
 *
 * Each caller supplies only the columns that are meaningful for its layer. The
 * fallback is always the raw payload, because a field this console does not
 * recognise is exactly what an engineer validating a new build needs to see.
 */

import { useMemo, useState } from 'react';
import { Box, Stack, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import type { Channel, TapMessage } from '@contract/types';
import { useChannel, useConsole } from '@state/ConsoleProvider';
import {
  ChannelChip,
  Column,
  DataTable,
  DetailPane,
  JsonView,
  Mono,
  PanelShell,
  StatTile,
  Unavailable,
} from '@components/primitives';
import { semantic } from '@/theme/theme';

export interface ChannelPanelProps {
  title: string;
  channel: Channel;
  /** Extra columns, rendered before the always-present type/frame columns. */
  columns?: Column<TapMessage>[];
  /** Types to show by default. Others remain reachable via the filter. */
  types?: string[];
  note?: string;
  emptyWhat: string;
  emptyNote?: string;
}

export function ChannelPanel({
  title,
  channel,
  columns = [],
  types,
  note,
  emptyWhat,
  emptyNote,
}: ChannelPanelProps) {
  const { sessionId, session } = useConsole();
  const messages = useChannel(channel);
  const [selected, setSelected] = useState<TapMessage | null>(null);
  const [filter, setFilter] = useState<string>('all');

  const kinds = useMemo(() => {
    const seen = new Set<string>();
    for (const message of messages) {
      seen.add(eventName(message));
    }
    return Array.from(seen).sort();
  }, [messages]);

  const rows = useMemo(() => {
    const base = types?.length
      ? messages.filter((m) => types.includes(m.type) || types.includes(eventName(m)))
      : messages;
    const filtered = filter === 'all' ? base : base.filter((m) => eventName(m) === filter);
    // Newest first: an engineer watching a live replay reads the top of the list.
    return filtered.slice(-800).reverse();
  }, [messages, types, filter]);

  if (!sessionId) {
    return (
      <PanelShell title={title}>
        <Unavailable what={title} reason="No session is open." />
      </PanelShell>
    );
  }

  if (channel === 'event' && session && !session.events_attached) {
    return (
      <PanelShell title={title} actions={<ChannelChip channel={channel} />}>
        <Unavailable
          what="Architecture events"
          reason={session.events_unavailable_reason ?? undefined}
        />
      </PanelShell>
    );
  }

  const allColumns: Column<TapMessage>[] = [
    {
      key: 'seq',
      header: 'seq',
      width: 64,
      render: (row) => <Mono colour={semantic.unknown}>{row.seq}</Mono>,
    },
    {
      key: 'frame',
      header: 'frame',
      width: 64,
      render: (row) => <Mono>{row.frame_index ?? '—'}</Mono>,
    },
    {
      key: 'type',
      header: 'type',
      width: 220,
      render: (row) => <Mono colour={typeColour(row)}>{eventName(row)}</Mono>,
    },
    ...columns,
  ];

  return (
    <PanelShell
      title={title}
      subtitle={`${messages.length} messages`}
      actions={<ChannelChip channel={channel} />}
      dense
    >
      <Stack sx={{ height: '100%', minHeight: 0 }}>
        {note ? (
          <Typography variant="caption" sx={{ px: 1.5, py: 0.75, borderBottom: '1px solid', borderColor: 'divider' }}>
            {note}
          </Typography>
        ) : null}

        {kinds.length > 1 ? (
          <Box sx={{ px: 1, py: 0.75, borderBottom: '1px solid', borderColor: 'divider', overflowX: 'auto' }}>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={filter}
              onChange={(_, value) => setFilter(value ?? 'all')}
            >
              <ToggleButton value="all">all ({messages.length})</ToggleButton>
              {kinds.map((kind) => (
                <ToggleButton key={kind} value={kind}>
                  {kind}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Box>
        ) : null}

        {/* `minHeight` keeps the list reachable when the detail pane is open. */}
        <Box sx={{ flex: 1, minHeight: 120, overflow: 'auto' }}>
          <DataTable
            rows={rows}
            columns={allColumns}
            emptyWhat={emptyWhat}
            emptyNote={emptyNote}
            onRowClick={setSelected}
            rowKey={(row) => String(row.seq)}
            selectedKey={selected ? String(selected.seq) : undefined}
          />
        </Box>

        {selected ? (
          <DetailPane
            title={eventName(selected)}
            subtitle={`seq ${selected.seq}`}
            onClose={() => setSelected(null)}
            storageKey={`channel.${channel}`}
            maxHeight="50%"
          >
            <Stack direction="row" spacing={1} sx={{ mb: 0.75 }} alignItems="center">
              <StatTile label="seq" value={selected.seq} />
              <StatTile label="type" value={eventName(selected)} />
              <StatTile label="frame" value={selected.frame_index ?? '—'} />
              <StatTile label="channel" value={selected.channel} />
            </Stack>
            <JsonView value={selected.payload} maxHeight={260} />
          </DetailPane>
        ) : null}
      </Stack>
    </PanelShell>
  );
}

export function eventName(message: TapMessage): string {
  if (message.type === 'event') {
    const type = (message.payload as Record<string, unknown>)?.event_type;
    return typeof type === 'string' ? type : 'event';
  }
  return message.type;
}

function typeColour(message: TapMessage): string | undefined {
  const name = eventName(message);
  if (message.type === 'gap') return semantic.gap;
  if (name.includes('failed') || name.includes('lost') || name.includes('degraded')) {
    return semantic.blind;
  }
  if (
    name.includes('warning') ||
    name.includes('suspected') ||
    name.includes('exhausted') ||
    name.includes('spike') ||
    name.includes('capped') ||
    name.includes('fallback')
  ) {
    return semantic.degraded;
  }
  return undefined;
}

/** `payload.<key>` as a monospaced cell. */
export function field(key: string, header = key, width?: number): Column<TapMessage> {
  return {
    key,
    header,
    width,
    render: (row) => {
      const value = (row.payload as Record<string, unknown>)?.[key];
      if (value === undefined || value === null) return <Mono colour={semantic.unknown}>—</Mono>;
      if (typeof value === 'number') return <Mono>{formatNumber(value)}</Mono>;
      if (typeof value === 'boolean') return <Mono>{value ? 'yes' : 'no'}</Mono>;
      if (typeof value === 'object') return <Mono>{JSON.stringify(value).slice(0, 48)}</Mono>;
      return <Mono>{String(value)}</Mono>;
    },
  };
}

function formatNumber(value: number): string {
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(3);
}
