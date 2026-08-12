/**
 * Shared display primitives.
 *
 * Every one of these renders a value it was given. None computes a verdict,
 * derives a threshold, or decides what a number means — that would be business
 * logic, and `tests/architecture/no-business-logic.test.ts` fails the build for
 * it.
 *
 * Two carry real weight and are worth reading before use:
 *
 * - `Unavailable` — the component that keeps V8 honest. "No data" and "could not
 *   look" must never render the same, so an empty list and an unavailable
 *   capability have different components.
 * - `CoverageBadge` — coverage accompanies every state answer. A state view that
 *   forgets to render it is a bug the coverage-invariant test catches.
 */

import {
  Alert,
  Box,
  Chip,
  IconButton,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import CloseIcon from '@mui/icons-material/Close';
import FullscreenIcon from '@mui/icons-material/Fullscreen';
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import type { CoverageSummary } from '@contract/types';
import { channelColour, mono, semantic } from '@/theme/theme';

// --- layout ------------------------------------------------------------------ //

export function PanelShell({
  title,
  subtitle,
  actions,
  children,
  dense,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  dense?: boolean;
}) {
  return (
    <Paper
      elevation={0}
      sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          px: 1.5,
          py: 1,
          borderBottom: '1px solid',
          borderColor: 'divider',
          flexShrink: 0,
        }}
      >
        <Typography variant="h3" sx={{ textTransform: 'uppercase' }}>
          {title}
        </Typography>
        {subtitle ? (
          <Typography variant="caption" sx={{ fontFamily: mono }}>
            {subtitle}
          </Typography>
        ) : null}
        <Box sx={{ flex: 1 }} />
        {actions}
      </Box>
      {/* `position: relative` so a maximized DetailPane can fill exactly this
          area — the panel body — rather than the viewport. */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          p: dense ? 0 : 1.25,
          position: 'relative',
        }}
      >
        {children}
      </Box>
    </Paper>
  );
}

/** Neither the list nor the inspector may be dragged below this, in pixels. */
const MIN_PANE_PX = 120;
const MIN_LIST_PX = 96;
const KEYBOARD_STEP_PX = 32;

/** Where a resize starts from when the pane has never been measured. */
const DEFAULT_PANE_PX = 280;

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(value, high));
}

function readStoredHeight(key: string | undefined): number | null {
  if (!key) return null;
  try {
    const raw = window.localStorage.getItem(`vosvc.split.${key}`);
    const parsed = raw === null ? Number.NaN : Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  } catch {
    // A browser with storage disabled still gets a working splitter, just one
    // that forgets between sessions.
    return null;
  }
}

function writeStoredHeight(key: string | undefined, value: number | null): void {
  if (!key) return;
  try {
    if (value === null) window.localStorage.removeItem(`vosvc.split.${key}`);
    else window.localStorage.setItem(`vosvc.split.${key}`, String(Math.round(value)));
  } catch {
    /* storage is a convenience, never a requirement */
  }
}

/**
 * The inspector drawer that opens under a list.
 *
 * Three things it must always do, each learned from getting it wrong:
 *
 * 1. **Offer a way back.** A drawer you can open and not close is a dead end.
 *    Back button, close button, and `Escape` — three, because a single
 *    affordance is a single point of failure in a tool used under pressure.
 * 2. **Never starve the list.** Capped and `flexShrink: 0`, so the rows above
 *    stay reachable however tall the content grows. An inspector that hides the
 *    thing you are inspecting from is not an inspector.
 * 3. **Let the engineer choose the split.** A record's useful size varies by two
 *    orders of magnitude — a coverage envelope against a full observation with
 *    trajectory and provenance — so a fixed ratio is wrong for almost every
 *    record. The top border is a drag handle, and the chosen height is
 *    remembered per panel.
 */
export function DetailPane({
  title,
  subtitle,
  onClose,
  actions,
  children,
  maxHeight = '58%',
  storageKey,
  resizable = true,
}: {
  title: string;
  subtitle?: ReactNode;
  onClose: () => void;
  actions?: ReactNode;
  children: ReactNode;
  maxHeight?: number | string;
  /** Remembers the drag position per panel. Omit to make the size ephemeral. */
  storageKey?: string;
  resizable?: boolean;
}) {
  const paneRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | null>(() => readStoredHeight(storageKey));
  const [dragging, setDragging] = useState(false);
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Escape steps back one level rather than dumping the engineer straight
      // out of a record they were reading: un-maximize first, close second.
      if (maximized) setMaximized(false);
      else onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, maximized]);

  /** Bounds for this drag, measured from the actual container. */
  const bounds = useCallback((): { min: number; max: number } => {
    const parent = paneRef.current?.parentElement;
    const available = parent?.getBoundingClientRect().height || window.innerHeight || 600;
    return { min: MIN_PANE_PX, max: Math.max(MIN_PANE_PX, available - MIN_LIST_PX) };
  }, []);

  /**
   * The pane's current height in pixels.
   *
   * A measured height of exactly `0` means "not laid out yet", not "zero tall",
   * so it must not be taken at face value — `??` would accept it, since `0` is
   * neither null nor undefined, and every keyboard resize would then start from
   * the floor regardless of how tall the pane actually is.
   */
  const currentHeight = useCallback((): number => {
    if (height !== null) return height;
    const measured = paneRef.current?.getBoundingClientRect().height ?? 0;
    return measured > 0 ? measured : DEFAULT_PANE_PX;
  }, [height]);

  const commit = useCallback(
    (next: number | null) => {
      setHeight(next);
      writeStoredHeight(storageKey, next);
    },
    [storageKey],
  );

  const startDrag = useCallback(
    (event: React.PointerEvent) => {
      if (!resizable) return;
      event.preventDefault();
      const startY = event.clientY;
      const startHeight = currentHeight();
      const { min, max } = bounds();
      setDragging(true);

      // Listeners go on `window`, not the handle: a fast drag outruns the
      // element and the pane would stick mid-motion if it owned them.
      const onMove = (move: PointerEvent) => {
        // Dragging up makes the inspector taller, which is the direction that
        // matches the border moving under the cursor.
        setHeight(clamp(startHeight + (startY - move.clientY), min, max));
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        setDragging(false);
        setHeight((current) => {
          writeStoredHeight(storageKey, current);
          return current;
        });
      };

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    },
    [bounds, currentHeight, resizable, storageKey],
  );

  const onHandleKey = useCallback(
    (event: React.KeyboardEvent) => {
      if (!resizable) return;
      const { min, max } = bounds();
      const current = currentHeight();

      if (event.key === 'ArrowUp') {
        event.preventDefault();
        commit(clamp(current + KEYBOARD_STEP_PX, min, max));
      } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        commit(clamp(current - KEYBOARD_STEP_PX, min, max));
      } else if (event.key === 'Home') {
        event.preventDefault();
        commit(null);
      }
    },
    [bounds, commit, currentHeight, resizable],
  );

  return (
    <Box
      ref={paneRef}
      sx={{
        borderTop: resizable && !maximized ? 'none' : '2px solid',
        borderColor: 'primary.main',
        bgcolor: 'background.paper',
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
        minHeight: 0,
        // Maximized fills the panel body exactly — `PanelShell` marks that box
        // `position: relative` so this covers the list without escaping into
        // the rest of the app.
        ...(maximized
          ? { position: 'absolute', inset: 0, zIndex: 5, maxHeight: 'none' }
          : height === null
            ? { maxHeight }
            : { height, maxHeight: 'none' }),
      }}
    >
      {resizable && !maximized ? (
        <Box
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize detail pane"
          tabIndex={0}
          onPointerDown={startDrag}
          onKeyDown={onHandleKey}
          onDoubleClick={() => commit(null)}
          title="Drag to resize · double-click to reset · ↑ ↓ to nudge"
          sx={{
            height: 10,
            flexShrink: 0,
            cursor: 'row-resize',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderTop: '2px solid',
            borderColor: dragging ? 'secondary.main' : 'primary.main',
            bgcolor: dragging ? 'action.selected' : 'transparent',
            transition: 'background-color 120ms, border-color 120ms',
            '&:hover': { bgcolor: 'action.hover' },
            '&:focus-visible': { outline: '2px solid', outlineColor: 'secondary.main' },
            touchAction: 'none',
          }}
        >
          <Box
            sx={{
              width: 34,
              height: 3,
              borderRadius: 2,
              bgcolor: dragging ? 'secondary.main' : 'divider',
            }}
          />
        </Box>
      ) : null}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          px: 1,
          py: 0.75,
          borderBottom: '1px solid',
          borderColor: 'divider',
          flexShrink: 0,
          position: 'sticky',
          top: 0,
          zIndex: 2,
          bgcolor: 'background.paper',
        }}
      >
        <Tooltip title="Back to the list (Esc)" arrow>
          <IconButton size="small" onClick={onClose} aria-label="Back to list">
            <ArrowBackIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Typography variant="h3">{title}</Typography>
        {subtitle ? (
          <Typography variant="caption" sx={{ fontFamily: mono }}>
            {subtitle}
          </Typography>
        ) : null}
        <Box sx={{ flex: 1 }} />
        {actions}
        <Tooltip title={maximized ? 'Restore split view' : 'Expand to fill the panel'} arrow>
          <IconButton
            size="small"
            onClick={() => setMaximized((on) => !on)}
            aria-label={maximized ? 'Restore detail pane' : 'Maximise detail pane'}
          >
            {maximized ? <FullscreenExitIcon fontSize="small" /> : <FullscreenIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
        <Tooltip title="Close (Esc)" arrow>
          <IconButton size="small" onClick={onClose} aria-label="Close detail">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
      <Box sx={{ flex: 1, minHeight: 0, overflow: 'auto', p: 1 }}>{children}</Box>
    </Box>
  );
}

export function StatTile({
  label,
  value,
  unit,
  hint,
  tone = 'default',
  dense = false,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: string;
  tone?: 'default' | 'good' | 'warn' | 'bad' | 'unknown';
  /**
   * Label and value on one line.
   *
   * Vertical space in this console is the scarce resource: a full-height tile
   * row costs ~60px, and that came straight out of the inspector an engineer is
   * trying to read. Dense tiles cost ~24px and say the same thing.
   */
  dense?: boolean;
}) {
  const colour = {
    default: 'text.primary',
    good: semantic.observing,
    warn: semantic.degraded,
    bad: semantic.blind,
    unknown: semantic.unknown,
  }[tone];

  const tile = dense ? (
    <Box
      sx={{
        display: 'inline-flex',
        alignItems: 'baseline',
        gap: 0.75,
        px: 1,
        py: 0.25,
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: 1,
      }}
    >
      <Typography variant="caption" sx={{ lineHeight: 1.4 }}>
        {label}
      </Typography>
      <Typography sx={{ fontFamily: mono, fontSize: '0.82rem', fontWeight: 600, color: colour }}>
        {value}
        {unit ? (
          <Typography component="span" variant="caption" sx={{ ml: 0.25, fontFamily: mono }}>
            {unit}
          </Typography>
        ) : null}
      </Typography>
    </Box>
  ) : (
    <Paper elevation={0} sx={{ px: 1.5, py: 1, minWidth: 108 }}>
      <Typography variant="caption" sx={{ display: 'block', lineHeight: 1.2 }}>
        {label}
      </Typography>
      <Typography sx={{ fontFamily: mono, fontSize: '1.05rem', fontWeight: 600, color: colour }}>
        {value}
        {unit ? (
          <Typography component="span" variant="caption" sx={{ ml: 0.5, fontFamily: mono }}>
            {unit}
          </Typography>
        ) : null}
      </Typography>
    </Paper>
  );

  return hint ? (
    <Tooltip title={hint} arrow>
      {tile}
    </Tooltip>
  ) : (
    tile
  );
}

// --- absence ------------------------------------------------------------------ //

/**
 * "The platform could not look" — categorically different from "nothing was
 * there", and rendered differently on purpose.
 *
 * > V8: *"Absence of observation ≠ observation of absence."*
 */
export function Unavailable({ reason, what }: { reason?: string | null; what: string }) {
  return (
    <Alert
      severity="warning"
      variant="outlined"
      sx={{ borderColor: semantic.degraded, '& .MuiAlert-message': { fontSize: '0.78rem' } }}
    >
      <strong>{what} unavailable.</strong>{' '}
      {reason ?? 'No reason was supplied by the harness.'}
      <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
        This is not an empty result. The platform could not be asked, or could not answer.
      </Typography>
    </Alert>
  );
}

/** "Nothing was here" — a real, complete answer. */
export function EmptyResult({ what, note }: { what: string; note?: string }) {
  return (
    <Box sx={{ p: 2, textAlign: 'center' }}>
      <Typography variant="body2" color="text.secondary">
        No {what}.
      </Typography>
      {note ? (
        <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
          {note}
        </Typography>
      ) : null}
    </Box>
  );
}

// --- coverage ------------------------------------------------------------------ //

export function CoverageBadge({ coverage }: { coverage: CoverageSummary | undefined }) {
  if (!coverage) {
    return (
      <Tooltip
        title="This result arrived without coverage. The platform returns it unconditionally, so its absence is a contract violation."
        arrow
      >
        <Chip size="small" label="COVERAGE MISSING" sx={{ bgcolor: semantic.blind, color: '#fff' }} />
      </Tooltip>
    );
  }

  const fraction = coverage.observable_fraction;
  const full = fraction >= 1 && coverage.unavailable.length === 0;
  const colour = full ? semantic.observing : fraction > 0 ? semantic.degraded : semantic.blind;

  return (
    <Tooltip
      arrow
      title={
        <Box>
          <div>observable fraction: {fraction.toFixed(3)}</div>
          <div>observing {coverage.cameras_observing} · degraded {coverage.cameras_degraded} · blind {coverage.cameras_blind}</div>
          {coverage.unavailable.length > 0 && (
            <div>unavailable: {coverage.unavailable.map(([c, r]) => `${c} (${r})`).join(', ')}</div>
          )}
          <Box sx={{ mt: 0.5, opacity: 0.8 }}>
            An empty result below full coverage does not mean nothing happened. It means
            nothing was observed.
          </Box>
        </Box>
      }
    >
      <Chip
        size="small"
        label={`coverage ${(fraction * 100).toFixed(0)}%`}
        sx={{ bgcolor: 'transparent', color: colour, border: `1px solid ${colour}` }}
      />
    </Tooltip>
  );
}

export function ChannelChip({ channel }: { channel: string }) {
  const colour = channelColour[channel] ?? semantic.unknown;
  return (
    <Chip
      size="small"
      label={channel}
      sx={{ bgcolor: 'transparent', color: colour, border: `1px solid ${colour}55` }}
    />
  );
}

export function VerdictChip({ verdict }: { verdict: string }) {
  const map: Record<string, string> = {
    validated: semantic.observing,
    pass: semantic.observing,
    deterministic: semantic.observing,
    unvalidated: semantic.blind,
    fail: semantic.blind,
    MISMATCH: semantic.blind,
    not_reached: semantic.degraded,
    observational: semantic.unknown,
    indeterminate: semantic.degraded,
    external: semantic.unknown,
    'review-required': semantic.degraded,
    'suitable-for-validation': semantic.observing,
  };
  const colour = map[verdict] ?? semantic.unknown;
  return (
    <Chip
      size="small"
      label={verdict}
      sx={{ bgcolor: `${colour}22`, color: colour, border: `1px solid ${colour}` }}
    />
  );
}

// --- data ---------------------------------------------------------------------- //

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  width?: number | string;
}

export function DataTable<T>({
  rows,
  columns,
  emptyWhat,
  emptyNote,
  onRowClick,
  selectedKey,
  rowKey,
}: {
  rows: T[];
  columns: Column<T>[];
  emptyWhat: string;
  emptyNote?: string;
  onRowClick?: (row: T) => void;
  selectedKey?: string;
  rowKey?: (row: T, index: number) => string;
}) {
  if (rows.length === 0) return <EmptyResult what={emptyWhat} note={emptyNote} />;

  return (
    <TableContainer sx={{ maxHeight: '100%' }}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            {columns.map((column) => (
              <TableCell key={column.key} sx={{ width: column.width }}>
                {column.header}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row, index) => {
            const key = rowKey?.(row, index) ?? String(index);
            return (
              <TableRow
                key={key}
                hover
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                selected={selectedKey === key}
                sx={{ cursor: onRowClick ? 'pointer' : 'default' }}
              >
                {columns.map((column) => (
                  <TableCell key={column.key}>{column.render(row)}</TableCell>
                ))}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

/**
 * A raw value inspector.
 *
 * Renders the payload verbatim. The console never hides a field it does not
 * recognise: an unfamiliar key is exactly what an engineer validating a new
 * Vision OS build most needs to see.
 */
export function JsonView({ value, maxHeight = 320 }: { value: unknown; maxHeight?: number | string }) {
  return (
    <Box
      component="pre"
      sx={{
        m: 0,
        p: 1,
        fontFamily: mono,
        // Sized to be read, not skimmed. An observation envelope is deeply
        // nested and an engineer reads it field by field; 0.72rem at 1.5 was
        // costing accuracy, not just comfort.
        fontSize: '0.8rem',
        lineHeight: 1.6,
        bgcolor: '#0d1117',
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: 1,
        overflow: 'auto',
        maxHeight,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}
    >
      {JSON.stringify(value, null, 2)}
    </Box>
  );
}

export function Mono({ children, colour }: { children: ReactNode; colour?: string }) {
  return (
    <Typography component="span" sx={{ fontFamily: mono, fontSize: '0.74rem', color: colour }}>
      {children}
    </Typography>
  );
}

export function Row({ children, gap = 1 }: { children: ReactNode; gap?: number }) {
  return (
    <Stack direction="row" spacing={gap} alignItems="center" flexWrap="wrap" useFlexGap>
      {children}
    </Stack>
  );
}

// --- formatting ------------------------------------------------------------------ //

/** Nanoseconds relative to a session origin. Absolute epochs are never rendered. */
export function relNs(ns: number, origin: number): string {
  const delta = (ns - origin) / 1_000_000;
  if (!Number.isFinite(delta)) return '—';
  return `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}ms`;
}

export function ms(value: number | undefined | null, digits = 1): string {
  if (value === undefined || value === null || !Number.isFinite(value)) return '—';
  return `${value.toFixed(digits)}`;
}

export function pct(value: number | undefined | null): string {
  if (value === undefined || value === null || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(1)}%`;
}

/** Confidence, with calibration made visible — an uncalibrated score is not comparable. */
export function ConfidenceCell({ confidence }: { confidence?: { value: number; calibrated: boolean } | null }) {
  if (!confidence) return <Mono colour={semantic.unknown}>—</Mono>;
  return (
    <Tooltip
      arrow
      title={
        confidence.calibrated
          ? 'Calibrated. Comparable across models.'
          : 'Uncalibrated. Not comparable across models (02_VOM §7.2) — do not threshold on this.'
      }
    >
      <span>
        <Mono colour={confidence.calibrated ? undefined : semantic.degraded}>
          {confidence.value.toFixed(3)}
          {confidence.calibrated ? '' : '~'}
        </Mono>
      </span>
    </Tooltip>
  );
}
