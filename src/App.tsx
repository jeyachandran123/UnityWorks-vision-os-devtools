/**
 * The console shell.
 *
 * Fourteen inspection panels, a pipeline view, an architecture screen, a
 * performance dashboard, failure injection and reports — with one replay
 * timeline pinned to the bottom, because every one of those views describes the
 * same frame and the engineer must never lose the playhead.
 */

import { useState } from 'react';
import {
  AppBar,
  Box,
  Chip,
  CssBaseline,
  Stack,
  Tab,
  Tabs,
  ThemeProvider,
  Toolbar,
  Tooltip,
  Typography,
} from '@mui/material';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { theme } from '@/theme/theme';
import { ConsoleProvider, useConsole } from '@state/ConsoleProvider';
import { SessionBar } from '@/pages/SessionBar';
import { Timeline } from '@/timeline/Timeline';
import { PipelineView } from '@/pipeline/PipelineView';
import { MetricsDashboard } from '@/metrics/MetricsDashboard';
import { FailureInjection } from '@/failure/FailureInjection';
import { ArchitectureValidation } from '@/architecture/ArchitectureValidation';
import { Reports } from '@/reports/Reports';
import { LiveVideoPanel } from '@panels/LiveVideoPanel';
import { ObservationsPanel } from '@panels/ObservationsPanel';
import { VisionStatePanel } from '@panels/VisionStatePanel';
import { DemandRegistryPanel } from '@panels/DemandRegistryPanel';
import {
  ArchitectureEventsPanel,
  CameraPanel,
  CanonicalCropsPanel,
  DetectionsPanel,
  FrameInformationPanel,
  HealthPanel,
  ObservationLogPanel,
  TracksPanel,
  UnderstandingPanel,
  VisionStateEventsPanel,
  VisualObjectsPanel,
} from '@panels/layerPanels';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A validation console must never show a cached answer as if it were
      // live: an engineer reading a stale projection would draw conclusions
      // about a world the platform has already moved past.
      staleTime: 0,
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});

/** The fourteen engineering panels, plus the four analysis screens. */
const VIEWS: Array<{ label: string; group: string; render: () => JSX.Element }> = [
  { label: 'Pipeline', group: 'overview', render: () => <PipelineView /> },
  { label: 'Live Video', group: 'panel', render: () => <LiveVideoPanel /> },
  { label: 'Frame Info', group: 'panel', render: () => <FrameInformationPanel /> },
  { label: 'Detections', group: 'panel', render: () => <DetectionsPanel /> },
  { label: 'Tracks', group: 'panel', render: () => <TracksPanel /> },
  { label: 'Visual Objects', group: 'panel', render: () => <VisualObjectsPanel /> },
  { label: 'Canonical Crops', group: 'panel', render: () => <CanonicalCropsPanel /> },
  { label: 'Understanding', group: 'panel', render: () => <UnderstandingPanel /> },
  { label: 'Observations', group: 'panel', render: () => <ObservationsPanel /> },
  { label: 'Vision State', group: 'panel', render: () => <VisionStatePanel /> },
  { label: 'Observation Log', group: 'panel', render: () => <ObservationLogPanel /> },
  { label: 'State Events', group: 'panel', render: () => <VisionStateEventsPanel /> },
  { label: 'Demand Registry', group: 'panel', render: () => <DemandRegistryPanel /> },
  { label: 'Camera', group: 'panel', render: () => <CameraPanel /> },
  { label: 'Health', group: 'panel', render: () => <HealthPanel /> },
  { label: 'Arch Events', group: 'panel', render: () => <ArchitectureEventsPanel /> },
  { label: 'Performance', group: 'analysis', render: () => <MetricsDashboard /> },
  { label: 'Failure Injection', group: 'analysis', render: () => <FailureInjection /> },
  { label: 'Architecture', group: 'analysis', render: () => <ArchitectureValidation /> },
  { label: 'Reports', group: 'analysis', render: () => <Reports /> },
];

function Shell() {
  const [view, setView] = useState(0);
  const { session } = useConsole();
  const active = VIEWS[view]!;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <AppBar position="static" elevation={0} sx={{ bgcolor: 'background.paper', borderBottom: '1px solid', borderColor: 'divider' }}>
        <Toolbar variant="dense" sx={{ gap: 2 }}>
          <Typography variant="h1" sx={{ fontSize: '0.95rem' }}>
            Vision OS Validation Console
          </Typography>
          <Chip size="small" label="ENGINEERING VALIDATION SYSTEM" color="primary" variant="outlined" />
          <Box sx={{ flex: 1 }} />
          <Tooltip
            arrow
            title="This console holds no business logic and writes nothing to Vision State. It speaks REST and WebSocket only."
          >
            <Chip size="small" label="read-only · public API only" variant="outlined" />
          </Tooltip>
          {session ? (
            <Typography variant="caption" sx={{ fontFamily: 'ui-monospace, monospace' }}>
              {session.camera_id} · {session.media_name}
            </Typography>
          ) : null}
        </Toolbar>
      </AppBar>

      <SessionBar />

      <Tabs
        value={view}
        onChange={(_, value) => setView(value)}
        variant="scrollable"
        scrollButtons="auto"
        sx={{ borderBottom: '1px solid', borderColor: 'divider', minHeight: 40, flexShrink: 0 }}
      >
        {VIEWS.map((entry) => (
          <Tab
            key={entry.label}
            label={
              <Stack direction="row" spacing={0.75} alignItems="center">
                <span>{entry.label}</span>
                {entry.group === 'analysis' ? (
                  <Box sx={{ width: 5, height: 5, borderRadius: '50%', bgcolor: 'secondary.main' }} />
                ) : null}
              </Stack>
            }
          />
        ))}
      </Tabs>

      <Box sx={{ flex: 1, minHeight: 0, p: 1 }}>{active.render()}</Box>

      <Timeline />
    </Box>
  );
}

export default function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <ConsoleProvider>
          <Shell />
        </ConsoleProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
