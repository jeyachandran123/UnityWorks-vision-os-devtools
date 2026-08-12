/**
 * The replay timeline and transport.
 *
 * Two properties make this a validation instrument rather than a video scrubber:
 *
 * 1. **A step runs the whole pipeline.** `step` grants the source a one-frame
 *    budget; the frame then travels acquisition → detection → tracking →
 *    registry → cropping → synthesis → state exactly as a played frame does. A
 *    playhead moving over pre-computed results would show the engineer something
 *    Vision OS never actually did.
 *
 * 2. **Gaps are drawn as absences.** A lost region is a coloured band, never an
 *    interpolation between the frames on either side. Drawing a smooth line
 *    across a gap would assert continuity the platform explicitly refused to
 *    claim (V8).
 */

import { useMemo, useRef } from 'react';
import {
  Box,
  ButtonGroup,
  IconButton,
  Slider,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import PauseIcon from '@mui/icons-material/Pause';
import SkipNextIcon from '@mui/icons-material/SkipNext';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import FirstPageIcon from '@mui/icons-material/FirstPage';
import { useConsole } from '@state/ConsoleProvider';
import { useFrameLedger, useTransport } from '@state/queries';
import { Mono, Row, StatTile } from '@components/primitives';
import { semantic } from '@/theme/theme';

const SPEEDS = [0.25, 0.5, 1, 2, 4, 8];

export function Timeline() {
  const { session, sessionId, gaps } = useConsole();
  const transport = useTransport();
  const { data: ledger } = useFrameLedger();

  const disabled = !sessionId || transport.isPending;
  const frameCount = session?.frame_count ?? 0;
  const index = session?.frame_index ?? 0;

  const faultFrames = useMemo(() => {
    const marks = new Map<number, string[]>();
    for (const entry of ledger?.entries ?? []) {
      if (entry.faults.length) marks.set(entry.frame_index, entry.faults);
    }
    return marks;
  }, [ledger]);

  return (
    <Box
      sx={{
        borderTop: '1px solid',
        borderColor: 'divider',
        bgcolor: 'background.paper',
        px: 1.5,
        py: 0.5,
        flexShrink: 0,
      }}
    >
      <Stack direction="row" spacing={2} alignItems="center">
        <ButtonGroup size="small" variant="outlined" disabled={disabled}>
          <Tooltip title="Seek to first frame">
            <span>
              <IconButton
                size="small"
                disabled={disabled}
                onClick={() => transport.mutate({ action: 'seek', detail: { frame_index: 0 } })}
              >
                <FirstPageIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title={session?.playing ? 'Pause' : 'Resume'}>
            <span>
              <IconButton
                size="small"
                disabled={disabled}
                onClick={() => transport.mutate({ action: session?.playing ? 'pause' : 'play' })}
              >
                {session?.playing ? <PauseIcon fontSize="small" /> : <PlayArrowIcon fontSize="small" />}
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Step one frame — through the entire pipeline, not just the playhead">
            <span>
              <IconButton
                size="small"
                disabled={disabled}
                onClick={() => transport.mutate({ action: 'step', detail: { count: 1 } })}
              >
                <SkipNextIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Restart: tear the pipeline down and re-attach it. Watch for the restart window recorded as a coverage observation.">
            <span>
              <IconButton
                size="small"
                disabled={disabled}
                onClick={() => transport.mutate({ action: 'restart' })}
              >
                <RestartAltIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </ButtonGroup>

        <Box sx={{ flex: 1, minWidth: 200 }}>
          <GapTrack frameCount={frameCount} gaps={gaps} faults={faultFrames} index={index} />
          <Slider
            size="small"
            min={0}
            max={Math.max(frameCount - 1, 0)}
            value={index}
            disabled={disabled || frameCount === 0}
            onChangeCommitted={(_, value) =>
              transport.mutate({ action: 'seek', detail: { frame_index: value as number } })
            }
            valueLabelDisplay="auto"
            sx={{ mt: -0.5 }}
          />
        </Box>

        <ToggleButtonGroup
          size="small"
          exclusive
          value={session?.speed ?? 1}
          disabled={disabled}
          onChange={(_, value) =>
            value && transport.mutate({ action: 'speed', detail: { speed: value } })
          }
        >
          {SPEEDS.map((speed) => (
            <ToggleButton key={speed} value={speed} sx={{ px: 1 }}>
              {speed}×
            </ToggleButton>
          ))}
        </ToggleButtonGroup>

        <Row gap={0.75}>
          <StatTile dense label="frame" value={`${index} / ${frameCount}`} />
          <StatTile
            dense
            label="state"
            value={session?.state ?? '—'}
            tone={session?.state === 'failed' ? 'bad' : session?.playing ? 'good' : 'default'}
          />
          <StatTile
            dense
            label="gaps"
            value={gaps.length}
            tone={gaps.length ? 'warn' : 'good'}
            hint="Detected from the message sequence. A gap is announced, never inferred from silence."
          />
        </Row>
      </Stack>

      {session?.exhausted ? (
        <Typography variant="caption" sx={{ color: semantic.unknown }}>
          End of media reached. This was a clean end-of-stream, not a connection loss — the source
          returned rather than raising, so the actor stopped instead of reconnecting.
        </Typography>
      ) : null}
    </Box>
  );
}

/**
 * The band above the scrubber: injected faults and delivery gaps, positioned by
 * frame. Gaps are drawn, never smoothed.
 */
function GapTrack({
  frameCount,
  gaps,
  faults,
  index,
}: {
  frameCount: number;
  gaps: Array<{ seq_from?: number; seq_to?: number; reason: string; recoverable: boolean }>;
  faults: Map<number, string[]>;
  index: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const width = Math.max(frameCount, 1);

  return (
    <Box
      ref={ref}
      sx={{
        position: 'relative',
        height: 14,
        borderRadius: 0.5,
        bgcolor: '#0d1117',
        border: '1px solid',
        borderColor: 'divider',
        overflow: 'hidden',
      }}
    >
      {Array.from(faults.entries()).map(([frame, kinds]) => (
        <Tooltip key={frame} title={`frame ${frame}: ${kinds.join(', ')}`} arrow>
          <Box
            sx={{
              position: 'absolute',
              left: `${(frame / width) * 100}%`,
              top: 0,
              bottom: 0,
              width: 2,
              bgcolor: semantic.injected,
            }}
          />
        </Tooltip>
      ))}

      {gaps.map((gap, i) => (
        <Tooltip
          key={i}
          arrow
          title={`gap: ${gap.reason} — ${gap.recoverable ? 'recoverable by query' : 'the platform was blind; nothing to fetch'}`}
        >
          <Box
            sx={{
              position: 'absolute',
              left: `${Math.min(((gap.seq_from ?? 0) / Math.max(width * 4, 1)) * 100, 98)}%`,
              top: 0,
              bottom: 0,
              width: 4,
              bgcolor: semantic.gap,
              opacity: 0.85,
            }}
          />
        </Tooltip>
      ))}

      <Box
        sx={{
          position: 'absolute',
          left: `${(index / width) * 100}%`,
          top: 0,
          bottom: 0,
          width: 1,
          bgcolor: '#e6edf3',
        }}
      />
    </Box>
  );
}

/** Frame-to-frame comparison: what changed between two points in the replay. */
export function FrameCompare() {
  const { buffer, revision } = useConsole();

  const rows = useMemo(() => {
    const entries = buffer.get('acquisition').slice(-2);
    if (entries.length < 2) return null;
    const [before, after] = entries as [typeof entries[0], typeof entries[0]];
    const a = before.payload as Record<string, unknown>;
    const b = after.payload as Record<string, unknown>;
    const keys = Array.from(new Set([...Object.keys(a), ...Object.keys(b)]));
    return keys
      .map((key) => ({ key, before: a[key], after: b[key] }))
      .filter((row) => JSON.stringify(row.before) !== JSON.stringify(row.after));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buffer, revision]);

  if (!rows) {
    return <Typography variant="caption">Two frames are needed to compare.</Typography>;
  }

  return (
    <Stack spacing={0.5}>
      {rows.map((row) => (
        <Stack key={row.key} direction="row" spacing={1}>
          <Mono colour={semantic.unknown}>{row.key}</Mono>
          <Mono>{JSON.stringify(row.before)}</Mono>
          <Mono colour={semantic.injected}>→ {JSON.stringify(row.after)}</Mono>
        </Stack>
      ))}
    </Stack>
  );
}
