/**
 * The pipeline visualization.
 *
 * Ten layers, each showing what it actually emitted this session. The point is
 * not the diagram — it is that a layer with **zero** traffic is visually
 * distinct from a layer that is unavailable, because those are different
 * problems and V8 says a tool must never conflate them.
 *
 * The ordering check is live: `observed_order` compares the sequence in which
 * layers first produced output against the declared order. A layer producing
 * before the layer that must feed it would mean a bypass, and that is exactly
 * what an engineer validating "no bypasses" needs to see.
 */

import { Box, Chip, Paper, Stack, Tooltip, Typography } from '@mui/material';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';
import { useConsole } from '@state/ConsoleProvider';
import { useArchitecture } from '@state/queries';
import { Mono, PanelShell, Unavailable } from '@components/primitives';
import { channelColour, mono, semantic } from '@/theme/theme';
import type { Channel } from '@contract/types';

interface Stage {
  channel: Channel;
  label: string;
  module: string;
  layer: string;
  owns: string;
}

/** The declared pipeline. Compared against runtime observation, never trusted alone. */
const STAGES: Stage[] = [
  { channel: 'camera', label: 'Camera', module: 'M1 Camera Manager', layer: 'L1', owns: 'camera records, calibration' },
  { channel: 'acquisition', label: 'Acquisition', module: 'M2/M3/M4', layer: 'L1', owns: 'frames, epochs, admission' },
  { channel: 'detection', label: 'Detection', module: 'M5 Detection Engine', layer: 'L2', owns: 'per-frame detections' },
  { channel: 'tracking', label: 'Tracking', module: 'M6 Tracking Engine', layer: 'L3', owns: 'track_id (epoch-scoped)' },
  { channel: 'registry', label: 'Object Registry', module: 'M7 Object Registry', layer: 'L4', owns: 'object_id — the only minter' },
  { channel: 'cropping', label: 'Crop Manager', module: 'M8 Crop Manager', layer: 'L5', owns: 'crops, attention budget' },
  { channel: 'understanding', label: 'Understanding', module: 'M9 Understanding Engine', layer: 'L5', owns: 'attribute values' },
  { channel: 'synthesis', label: 'Observation Builder', module: 'M11 Observation Builder', layer: 'L6', owns: 'observation_id' },
  { channel: 'state', label: 'Vision State', module: 'M12 Vision State Manager', layer: 'L6', owns: 'the log + projection' },
  { channel: 'observation', label: 'Observation API', module: 'M14 Observation API', layer: 'L7', owns: 'nothing — it only reads' },
];

export function PipelineView() {
  const { session, buffer, revision, sessionId } = useConsole();
  const { data: architecture } = useArchitecture();

  if (!sessionId) {
    return (
      <PanelShell title="Pipeline">
        <Unavailable what="Pipeline" reason="No session is open." />
      </PanelShell>
    );
  }

  const counts = buffer.counts();
  void revision;
  const observed = architecture?.runtime.observed_order ?? [];
  const inversions = observed.filter((entry) => entry.out_of_order);
  const eventsAttached = session?.events_attached ?? false;

  return (
    <PanelShell
      title="Pipeline"
      subtitle="camera → acquisition → detection → tracking → registry → crop → understanding → builder → state → API"
      actions={
        inversions.length > 0 ? (
          <Chip
            size="small"
            label={`${inversions.length} ORDERING INVERSION`}
            sx={{ bgcolor: semantic.blind, color: '#fff' }}
          />
        ) : (
          <Chip
            size="small"
            label="ordering consistent"
            sx={{ color: semantic.observing, border: `1px solid ${semantic.observing}` }}
          />
        )
      }
    >
      <Stack spacing={0.5}>
        {STAGES.map((stage, index) => {
          const count = counts[stage.channel] ?? 0;
          // Three states, deliberately distinguished:
          //   unavailable — we could not observe this layer at all
          //   silent      — we observed it and it produced nothing
          //   active      — it produced output
          const unavailable = !eventsAttached && isEventOnly(stage.channel);
          const tone = unavailable ? semantic.degraded : count > 0 ? channelColour[stage.channel] : semantic.unknown;
          const inverted = observed.find((o) => o.channel === stage.channel)?.out_of_order;

          return (
            <Box key={stage.channel}>
              <Paper
                elevation={0}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1.5,
                  px: 1.5,
                  py: 0.75,
                  borderLeft: `3px solid ${tone}`,
                  opacity: unavailable ? 0.7 : count > 0 ? 1 : 0.65,
                }}
              >
                <Chip size="small" label={stage.layer} sx={{ minWidth: 40 }} />
                <Box sx={{ minWidth: 170 }}>
                  <Typography sx={{ fontWeight: 600, fontSize: '0.82rem' }}>{stage.label}</Typography>
                  <Typography variant="caption" sx={{ fontFamily: mono }}>
                    {stage.module}
                  </Typography>
                </Box>

                <Tooltip title={`Owns: ${stage.owns}`} arrow>
                  <Box sx={{ flex: 1 }}>
                    <Mono colour={semantic.unknown}>owns {stage.owns}</Mono>
                  </Box>
                </Tooltip>

                {unavailable ? (
                  <Chip
                    size="small"
                    label="UNAVAILABLE"
                    sx={{ color: semantic.degraded, border: `1px solid ${semantic.degraded}` }}
                  />
                ) : (
                  <Chip
                    size="small"
                    label={count > 0 ? `${count} messages` : 'silent'}
                    sx={{
                      color: tone,
                      border: `1px solid ${tone}55`,
                      bgcolor: 'transparent',
                    }}
                  />
                )}

                {inverted ? (
                  <Tooltip title="This layer produced output before a layer that must feed it. That would mean a bypass." arrow>
                    <Chip size="small" label="OUT OF ORDER" sx={{ bgcolor: semantic.blind, color: '#fff' }} />
                  </Tooltip>
                ) : null}
              </Paper>

              {index < STAGES.length - 1 ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', my: -0.25 }}>
                  <ArrowDownwardIcon sx={{ fontSize: 14, color: 'divider' }} />
                </Box>
              ) : null}
            </Box>
          );
        })}
      </Stack>

      <Typography variant="caption" sx={{ display: 'block', mt: 1.5 }}>
        <strong>Silent ≠ unavailable.</strong> A silent layer was observed and produced nothing — a
        legitimate answer. An unavailable layer could not be observed at all, and nothing may be
        concluded from its emptiness.
      </Typography>
    </PanelShell>
  );
}

/** Layers whose only signal is the Event Bus; without it we truly cannot see them. */
function isEventOnly(channel: Channel): boolean {
  return channel === 'cropping' || channel === 'understanding' || channel === 'synthesis' || channel === 'camera';
}
