/**
 * Session control: pick media, open a session, watch the connection.
 *
 * The banner states the two things an engineer must know before trusting
 * anything else on screen: whether Vision OS actually loaded, and whether this
 * session's semantics permit a determinism claim.
 */

import { useRef, useState } from 'react';
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Chip,
  CircularProgress,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import IconButton from '@mui/material/IconButton';
import { useConsole } from '@state/ConsoleProvider';
import { useCreateSession, useHealth, useMedia, useSessions, useUploadMedia } from '@state/queries';
import { Mono } from '@components/primitives';
import { describeError, isUnreachable } from '@transport/errors';
import { semantic } from '@/theme/theme';

export function SessionBar() {
  const { sessionId, setSessionId, session, streamStatus } = useConsole();
  const { data: health, error: healthError } = useHealth();
  const { data: media, refetch: refetchMedia } = useMedia();
  const { data: sessions } = useSessions();
  const createSession = useCreateSession();
  const upload = useUploadMedia();
  const fileInput = useRef<HTMLInputElement>(null);

  const [mediaId, setMediaId] = useState('m-synthetic');
  const [fps, setFps] = useState('12');
  const [semantics, setSemantics] = useState<'archival' | 'realtime'>('archival');
  const [failure, setFailure] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  const assets = media?.media ?? [];
  const selected = assets.find((a) => a.media_id === mediaId);
  const visionOsDown = health && !health.vision_os.available;
  const unusable = assets.filter((a) => !a.usable);
  //
  // Whether the harness can open *any* container. Read from the capability
  // report rather than guessed from the failures, so "the codec is missing" is
  // never confused with "these four files happen to be corrupt".
  const containersDecodable = Object.values(media?.capabilities.containers ?? {}).some(Boolean);

  const open = () => {
    setFailure(null);
    createSession.mutate(
      {
        media_id: mediaId,
        target_fps: Number(fps) || 12,
        semantics,
        deterministic: true,
        autostart: false,
      },
      {
        onError: (error) => {
          const described = describeError(error);
          setFailure(`${described.code}: ${described.message}`);
        },
      },
    );
  };

  // The harness being absent must not look like a platform with nothing to say.
  // Without this the console renders empty panels and a bare "500", which is the
  // console committing the exact silent-failure V8 exists to prevent.
  if (isUnreachable(healthError)) {
    return (
      <Box sx={{ p: 1.25, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Alert severity="error" variant="outlined">
          <AlertTitle>The Validation Harness is not running</AlertTitle>
          This console is a viewer. It holds no data of its own and every panel is
          empty until the harness is up.
          <Box
            component="pre"
            sx={{
              mt: 1,
              mb: 1,
              p: 1,
              bgcolor: '#0d1117',
              border: '1px solid',
              borderColor: 'divider',
              borderRadius: 1,
              fontFamily: 'ui-monospace, monospace',
              fontSize: '0.75rem',
              overflowX: 'auto',
            }}
          >
            {'cd harness\npip install -e ".[dev]"\npython -m vosvc_harness'}
          </Box>
          <Typography variant="caption" sx={{ display: 'block' }}>
            The dev server proxies <Mono>/api</Mono> to{' '}
            <Mono>http://127.0.0.1:8808</Mono>. When nothing is listening there, the
            proxy answers <Mono>500</Mono> with an empty body — which is a
            connection failure, not a Vision OS failure. Nothing may be concluded
            about the platform from this screen.
          </Typography>
        </Alert>
      </Box>
    );
  }

  // Collapsed, the bar is one line showing what is running. Vertical space in a
  // 15-panel console is the scarce resource, and session setup is something an
  // engineer does once then reads for the next hour.
  if (collapsed) {
    return (
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        sx={{ px: 1.25, py: 0.5, borderBottom: '1px solid', borderColor: 'divider' }}
      >
        <Tooltip title="Show session controls" arrow>
          <IconButton size="small" onClick={() => setCollapsed(false)} aria-label="Expand session controls">
            <ExpandMoreIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Mono>{session ? `${session.session_id} · ${session.media_name}` : 'no session'}</Mono>
        {session ? (
          <Chip size="small" label={session.state} sx={{ color: semantic.observing }} />
        ) : null}
        <Box sx={{ flex: 1 }} />
        <Chip
          size="small"
          label={`stream ${streamStatus}`}
          sx={{
            color: streamStatus === 'open' ? semantic.observing : semantic.degraded,
            border: `1px solid ${streamStatus === 'open' ? semantic.observing : semantic.degraded}`,
          }}
        />
      </Stack>
    );
  }

  return (
    <Stack spacing={1} sx={{ px: 1.25, py: 0.75, borderBottom: '1px solid', borderColor: 'divider' }}>
      {visionOsDown ? (
        <Alert severity="error" variant="outlined">
          <strong>Vision OS did not load.</strong> <Mono>{health.vision_os.error}</Mono> — nothing on
          this screen describes a running platform.
        </Alert>
      ) : null}

      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Tooltip title="Collapse to one line" arrow>
          <IconButton size="small" onClick={() => setCollapsed(true)} aria-label="Collapse session controls">
            <ExpandLessIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <TextField
          select
          size="small"
          label="media"
          value={mediaId}
          onChange={(event) => setMediaId(event.target.value)}
          sx={{ minWidth: 240 }}
        >
          {assets.map((asset) => (
            <MenuItem key={asset.media_id} value={asset.media_id} disabled={!asset.usable}>
              <Stack sx={{ minWidth: 0 }}>
                <Box component="span">
                  {asset.name} · {asset.kind}
                  {asset.probe
                    ? ` · ${asset.probe.frame_count}f ${asset.probe.width}×${asset.probe.height} @${asset.probe.fps.toFixed(0)}fps`
                    : ''}
                </Box>
                {/* The reason travels with the item. A disabled option that
                    just says "unusable" cannot be clicked to find out why, so
                    the explanation has to be visible without selecting it. */}
                {!asset.usable && asset.error ? (
                  <Typography variant="caption" sx={{ color: semantic.degraded, whiteSpace: 'normal' }}>
                    {asset.error}
                  </Typography>
                ) : null}
              </Stack>
            </MenuItem>
          ))}
        </TextField>

        <input
          ref={fileInput}
          type="file"
          hidden
          accept=".mp4,.avi,.mkv,.mov,.webm,.m4v,.png,.jpg,.jpeg,.bmp,.ppm,.raw"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) upload.mutate(file, { onSuccess: () => refetchMedia() });
          }}
        />
        <Button
          size="small"
          variant="outlined"
          startIcon={upload.isPending ? <CircularProgress size={14} /> : <UploadFileIcon />}
          onClick={() => fileInput.current?.click()}
          disabled={upload.isPending}
        >
          Upload
        </Button>

        <TextField
          size="small"
          label="fps"
          value={fps}
          onChange={(event) => setFps(event.target.value)}
          sx={{ width: 80 }}
        />

        <Tooltip
          arrow
          title="archival protects completeness and is reproducible; realtime permits dropping. Determinism comparisons are only meaningful on archival."
        >
          <TextField
            select
            size="small"
            label="semantics"
            value={semantics}
            onChange={(event) => setSemantics(event.target.value as 'archival' | 'realtime')}
            sx={{ width: 130 }}
          >
            <MenuItem value="archival">archival</MenuItem>
            <MenuItem value="realtime">realtime</MenuItem>
          </TextField>
        </Tooltip>

        <Button
          variant="contained"
          size="small"
          onClick={open}
          disabled={createSession.isPending || !selected?.usable}
        >
          {createSession.isPending ? 'Booting…' : 'Open session'}
        </Button>

        <TextField
          select
          size="small"
          label="active session"
          value={sessionId ?? ''}
          onChange={(event) => setSessionId(event.target.value || null)}
          sx={{ minWidth: 240 }}
        >
          <MenuItem value="">— none —</MenuItem>
          {(sessions?.sessions ?? []).map((s) => (
            <MenuItem key={s.session_id} value={s.session_id}>
              {s.session_id} · {s.media_name} · {s.state}
            </MenuItem>
          ))}
        </TextField>

        <Box sx={{ flex: 1 }} />

        <Chip
          size="small"
          label={`stream ${streamStatus}`}
          sx={{
            color: streamStatus === 'open' ? semantic.observing : semantic.degraded,
            border: `1px solid ${streamStatus === 'open' ? semantic.observing : semantic.degraded}`,
          }}
        />
        {session ? (
          <Chip
            size="small"
            label={session.semantics}
            sx={{
              color: session.semantics === 'archival' ? semantic.observing : semantic.degraded,
              border: '1px solid currentColor',
            }}
          />
        ) : null}
        {health ? (
          <Tooltip
            arrow
            title={
              containersDecodable
                ? `Decoding backends: ${health.media.backends.join(', ')} — mp4/avi/mkv can be opened`
                : `Decoding backends: ${health.media.backends.join(', ')} — no container codec, so mp4/avi/mkv cannot be opened`
            }
          >
            <Chip
              size="small"
              label={`backends: ${health.media.backends.join('/')}`}
              sx={{
                color: containersDecodable ? semantic.observing : semantic.degraded,
                border: `1px solid ${containersDecodable ? semantic.observing : semantic.degraded}`,
              }}
            />
          </Tooltip>
        ) : null}
      </Stack>

      {selected && !selected.usable ? (
        <Alert severity="warning" variant="outlined">
          <strong>{selected.name} cannot be replayed.</strong> {selected.error}
        </Alert>
      ) : null}

      {/* A container the harness cannot open is a capability gap, and a gap has
          to state its own remedy. Silently listing four greyed-out files tells
          an engineer nothing except that something is broken. */}
      {unusable.length > 0 && !containersDecodable ? (
        <Alert severity="warning" variant="outlined">
          <strong>
            {unusable.length} video file{unusable.length === 1 ? '' : 's'} cannot be decoded.
          </strong>{' '}
          No video codec is installed on the harness, so <Mono>.mp4</Mono> / <Mono>.avi</Mono> /{' '}
          <Mono>.mkv</Mono> cannot be opened. Synthetic, frame-folder and <Mono>.raw</Mono> sources
          still work.
          <Box
            component="pre"
            sx={{
              mt: 0.75,
              mb: 0,
              p: 1,
              bgcolor: '#0d1117',
              border: '1px solid',
              borderColor: 'divider',
              borderRadius: 1,
              fontFamily: 'ui-monospace, monospace',
              fontSize: '0.75rem',
              overflowX: 'auto',
            }}
          >
            {'pip install "av>=12.0"      # then restart the harness'}
          </Box>
        </Alert>
      ) : null}

      {failure ? (
        <Alert severity="error" variant="outlined">
          {failure}
        </Alert>
      ) : null}

      {session?.state === 'failed' ? (
        <Alert severity="error" variant="outlined">
          Session failed: <Mono>{session.error}</Mono>
        </Alert>
      ) : null}

      {session && !session.events_attached ? (
        <Alert severity="warning" variant="outlined">
          <strong>Architecture events unavailable.</strong> {session.events_unavailable_reason} —
          layers that report only through the Event Bus will show as UNAVAILABLE rather than empty.
        </Alert>
      ) : null}
    </Stack>
  );
}
