/**
 * Component tests.
 *
 * The assertions that matter here are all about *absence*: that "we could not
 * look" and "nothing was there" never render the same, and that a missing
 * coverage report is loud rather than invisible.
 */

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider } from '@mui/material';
import {
  ConfidenceCell,
  CoverageBadge,
  EmptyResult,
  DataTable,
  Unavailable,
  VerdictChip,
} from '@components/primitives';
import { theme } from '@/theme/theme';
import { coverage } from '@simulator/simulator';

const wrap = (ui: React.ReactElement) =>
  render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>);

describe('absence is never ambiguous', () => {
  it('renders an unavailable capability distinctly from an empty result', () => {
    const { unmount } = wrap(<Unavailable what="Detections" reason="event bus not attached" />);
    expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/could not be asked/i)).toBeInTheDocument();
    unmount();

    wrap(<EmptyResult what="detections" />);
    expect(screen.getByText(/No detections/i)).toBeInTheDocument();
    expect(screen.queryByText(/could not be asked/i)).not.toBeInTheDocument();
  });

  it('always surfaces the reason an unavailable thing is unavailable', () => {
    wrap(<Unavailable what="Metrics" reason="MetricsEngine exposes no readable accessor" />);
    expect(screen.getByText(/no readable accessor/i)).toBeInTheDocument();
  });
});

describe('CoverageBadge', () => {
  it('shouts when coverage is missing entirely', () => {
    wrap(<CoverageBadge coverage={undefined} />);
    // Coverage is returned unconditionally by the platform; its absence is a
    // contract violation, not a quiet default.
    expect(screen.getByText('COVERAGE MISSING')).toBeInTheDocument();
  });

  it('renders full and partial coverage differently', () => {
    const { unmount } = wrap(<CoverageBadge coverage={coverage()} />);
    expect(screen.getByText('coverage 100%')).toBeInTheDocument();
    unmount();

    wrap(<CoverageBadge coverage={coverage({ observable_fraction: 0.42 })} />);
    expect(screen.getByText('coverage 42%')).toBeInTheDocument();
  });
});

describe('ConfidenceCell', () => {
  it('marks an uncalibrated score so it is not compared across models', () => {
    wrap(<ConfidenceCell confidence={{ value: 0.8, calibrated: false }} />);
    expect(screen.getByText('0.800~')).toBeInTheDocument();
  });

  it('renders a calibrated score plainly', () => {
    wrap(<ConfidenceCell confidence={{ value: 0.8, calibrated: true }} />);
    expect(screen.getByText('0.800')).toBeInTheDocument();
  });

  it('renders no confidence as unknown rather than zero', () => {
    wrap(<ConfidenceCell confidence={null} />);
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('0.000')).not.toBeInTheDocument();
  });
});

describe('VerdictChip', () => {
  it('does not present not_reached or unvalidated as a pass', () => {
    for (const verdict of ['unvalidated', 'not_reached', 'MISMATCH', 'fail']) {
      const { unmount } = wrap(<VerdictChip verdict={verdict} />);
      expect(screen.getByText(verdict)).toBeInTheDocument();
      unmount();
    }
  });
});

describe('DataTable', () => {
  it('renders rows with the supplied columns', () => {
    wrap(
      <DataTable
        rows={[{ id: 'a', n: 1 }, { id: 'b', n: 2 }]}
        columns={[
          { key: 'id', header: 'id', render: (r) => <span>{r.id}</span> },
          { key: 'n', header: 'n', render: (r) => <span>{r.n}</span> },
        ]}
        emptyWhat="rows"
        rowKey={(r) => r.id}
      />,
    );
    expect(screen.getByText('a')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('falls back to an empty result, not a blank table', () => {
    wrap(
      <DataTable
        rows={[]}
        columns={[{ key: 'id', header: 'id', render: () => null }]}
        emptyWhat="observations"
        emptyNote="Suppression is the designed behaviour."
      />,
    );
    expect(screen.getByText(/No observations/i)).toBeInTheDocument();
    expect(screen.getByText(/Suppression is the designed behaviour/)).toBeInTheDocument();
  });
});
