/**
 * An instrument panel, not a product surface.
 *
 * Dark, dense, monospaced where values matter. Colour carries exactly one
 * meaning — observability state — and is never decorative, because an engineer
 * scanning fourteen panels for a problem must be able to trust that orange means
 * something.
 *
 * Deliberately shares nothing with the production frontend. No component, token,
 * or stylesheet is imported from it; the two are allowed to diverge forever.
 */

import { createTheme } from '@mui/material/styles';

export const semantic = {
  /** The platform can see. */
  observing: '#3fb950',
  /** The platform is degraded but running (V9). */
  degraded: '#d29922',
  /** The platform cannot see. Never the same colour as "nothing was there" (V8). */
  blind: '#f85149',
  /** A gap: messages were lost. Rendered as an absence, never interpolated. */
  gap: '#bc8cff',
  /** Unknown or unavailable — distinct from both "healthy" and "broken". */
  unknown: '#8b949e',
  /** An injected fault is active. */
  injected: '#58a6ff',
} as const;

export const channelColour: Record<string, string> = {
  camera: '#79c0ff',
  acquisition: '#56d364',
  detection: '#e3b341',
  tracking: '#ff9bce',
  registry: '#d2a8ff',
  cropping: '#ffa657',
  understanding: '#a5d6ff',
  synthesis: '#7ee787',
  state: '#f778ba',
  observation: '#58a6ff',
  demand: '#ffab70',
  metrics: '#8ddb8c',
  health: '#f0883e',
  event: '#8b949e',
  transport: '#6e7681',
};

export const theme = createTheme({
  palette: {
    mode: 'dark',
    background: { default: '#0d1117', paper: '#161b22' },
    primary: { main: '#58a6ff' },
    secondary: { main: '#d2a8ff' },
    success: { main: semantic.observing },
    warning: { main: semantic.degraded },
    error: { main: semantic.blind },
    divider: '#30363d',
    text: { primary: '#e6edf3', secondary: '#8b949e' },
  },
  typography: {
    fontFamily:
      '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif',
    fontSize: 13,
    h1: { fontSize: '1.35rem', fontWeight: 600 },
    h2: { fontSize: '1.05rem', fontWeight: 600 },
    h3: { fontSize: '0.9rem', fontWeight: 600, letterSpacing: '0.02em' },
    body2: { fontSize: '0.8rem' },
    caption: { fontSize: '0.72rem', color: '#8b949e' },
  },
  shape: { borderRadius: 6 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { backgroundColor: '#0d1117' },
        '::-webkit-scrollbar': { width: 10, height: 10 },
        '::-webkit-scrollbar-thumb': { background: '#30363d', borderRadius: 5 },
        '::-webkit-scrollbar-track': { background: 'transparent' },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: { backgroundImage: 'none', border: '1px solid #30363d' },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace', fontSize: '0.7rem' },
        sizeSmall: { height: 20 },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: {
          fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
          fontSize: '0.74rem',
          borderColor: '#21262d',
          padding: '4px 10px',
        },
        head: { fontWeight: 600, color: '#8b949e', background: '#0d1117' },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: { minHeight: 40, textTransform: 'none', fontSize: '0.8rem' },
      },
    },
    MuiButton: {
      styleOverrides: { root: { textTransform: 'none' } },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: { fontSize: '0.72rem', maxWidth: 380, lineHeight: 1.5 },
      },
    },
  },
});

export const mono = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';
