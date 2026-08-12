/**
 * Live Video — the replayed camera, and an honest banner about it.
 *
 * Frame serving is off by default (V12). When it is off this panel says so
 * plainly and stays useful: the frame *descriptor* is still shown, because
 * knowing that frame 412 was 6.2 MB, a keyframe, and carried an injected
 * occlusion is most of what an engineer needs even without the pixels.
 */

import { useState } from 'react';
import { Alert, Box, Button, Stack, TextField, Typography } from '@mui/material';
import { useConsole } from '@state/ConsoleProvider';
import { useHealth } from '@state/queries';
import { ChannelChip, Mono, PanelShell, Row, StatTile, Unavailable } from '@components/primitives';
import { semantic } from '@/theme/theme';
import type { FrameLedgerEntry } from '@contract/types';

export function LiveVideoPanel() {
  const { client, sessionId, session, latestOf } = useConsole();
  const { data: health } = useHealth();
  const [purpose, setPurpose] = useState('');
  const [confirmed, setConfirmed] = useState('');

  const serveFrames = health?.harness.serve_frames ?? false;
  const tap = latestOf('acquisition');
  const frame = tap?.payload as unknown as FrameLedgerEntry | undefined;
  const index = session?.frame_index ?? 0;

  if (!sessionId) {
    return (
      <PanelShell title="Live Video">
        <Unavailable what="Video" reason="No session is open. Open one from the Session bar." />
      </PanelShell>
    );
  }

  return (
    <PanelShell
      title="Live Video"
      subtitle={`frame ${index} / ${session?.frame_count ?? '?'}`}
      actions={<ChannelChip channel="acquisition" />}
    >
      <Stack spacing={1.25}>
        {!serveFrames ? (
          <Alert severity="info" variant="outlined">
            <strong>Frame serving is disabled.</strong> Pixels stay local by default (V12). Set{' '}
            <Mono>VOSVC_SERVE_FRAMES=1</Mono> on the harness to enable it — a deployment decision,
            not a console one.
          </Alert>
        ) : (
          <Alert
            severity="warning"
            variant="outlined"
            sx={{ borderColor: semantic.degraded }}
          >
            <strong>Imagery is being served.</strong> Every frame fetch is recorded with the purpose
            you declare. An engineering tool may see pixels; it may not do so quietly.
          </Alert>
        )}

        {serveFrames && !confirmed ? (
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              size="small"
              label="Purpose (required, recorded)"
              value={purpose}
              onChange={(event) => setPurpose(event.target.value)}
              sx={{ flex: 1 }}
            />
            <Button
              variant="outlined"
              disabled={!purpose.trim()}
              onClick={() => setConfirmed(purpose.trim())}
            >
              View frames
            </Button>
          </Stack>
        ) : null}

        {serveFrames && confirmed ? (
          <Box
            sx={{
              border: '1px solid',
              borderColor: 'divider',
              borderRadius: 1,
              overflow: 'hidden',
              bgcolor: '#000',
              display: 'flex',
              justifyContent: 'center',
            }}
          >
            <Box
              component="img"
              alt={`frame ${index}`}
              src={client.frameUrl(sessionId, index, confirmed)}
              sx={{ maxWidth: '100%', imageRendering: 'pixelated' }}
            />
          </Box>
        ) : null}

        <Row>
          <StatTile label="frame index" value={index} />
          <StatTile label="pts" value={frame?.pts_ms ?? '—'} unit="ms" />
          <StatTile
            label="geometry"
            value={frame ? `${frame.width}×${frame.height}` : '—'}
          />
          <StatTile label="bytes" value={frame ? frame.bytes.toLocaleString() : '—'} />
          <StatTile
            label="keyframe"
            value={frame ? (frame.is_keyframe ? 'yes' : 'no') : '—'}
          />
          <StatTile
            label="injected faults"
            value={frame?.faults.length ? frame.faults.join(', ') : 'none'}
            tone={frame?.faults.length ? 'warn' : 'default'}
            hint="Faults are injected at the P1/P2 port boundary, so the platform experiences a genuinely bad camera."
          />
        </Row>

        {session?.semantics === 'realtime' ? (
          <Alert severity="warning" variant="outlined">
            This session uses <Mono>realtime</Mono> semantics — the platform may drop frames.
            Determinism comparisons across it are not meaningful.
          </Alert>
        ) : null}

        <Typography variant="caption">
          The uploaded video is delivered through a P1 source adapter and decoded through a P2
          decoder. Vision OS cannot tell it from a camera.
        </Typography>
      </Stack>
    </PanelShell>
  );
}
