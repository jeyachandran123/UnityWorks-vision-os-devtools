/**
 * The inspector must always offer a way back.
 *
 * A drawer that opens and cannot be closed is a dead end, and this one shipped
 * as exactly that: clicking an observation opened the detail view, the list
 * collapsed underneath it, and there was no button, no key and no gesture that
 * returned you to the rows.
 *
 * Three affordances are asserted here because a single one is a single point of
 * failure in a tool people use under time pressure.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThemeProvider } from '@mui/material';
import { DetailPane } from '@components/primitives';
import { theme } from '@/theme/theme';

const wrap = (ui: React.ReactElement) =>
  render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>);

describe('DetailPane', () => {
  it('offers a close button', async () => {
    const onClose = vi.fn();
    wrap(
      <DetailPane title="Observation" onClose={onClose}>
        <div>body</div>
      </DetailPane>,
    );

    await userEvent.click(screen.getByLabelText('Close detail'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('offers a back button', async () => {
    const onClose = vi.fn();
    wrap(
      <DetailPane title="Observation" onClose={onClose}>
        <div>body</div>
      </DetailPane>,
    );

    await userEvent.click(screen.getByLabelText('Back to list'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    wrap(
      <DetailPane title="Observation" onClose={onClose}>
        <div>body</div>
      </DetailPane>,
    );

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('stops listening for Escape once unmounted', () => {
    const onClose = vi.fn();
    const { unmount } = wrap(
      <DetailPane title="Observation" onClose={onClose}>
        <div>body</div>
      </DetailPane>,
    );

    unmount();
    fireEvent.keyDown(window, { key: 'Escape' });
    // A leaked listener would close a pane the engineer reopened later, which
    // is worse than no shortcut at all.
    expect(onClose).not.toHaveBeenCalled();
  });

  it('renders its title, subtitle and actions', () => {
    wrap(
      <DetailPane
        title="Observation"
        subtitle="obs-000123"
        onClose={() => {}}
        actions={<button type="button">Raw JSON</button>}
      >
        <div>body</div>
      </DetailPane>,
    );

    expect(screen.getByText('Observation')).toBeInTheDocument();
    expect(screen.getByText('obs-000123')).toBeInTheDocument();
    expect(screen.getByText('Raw JSON')).toBeInTheDocument();
    expect(screen.getByText('body')).toBeInTheDocument();
  });

  it('does not grow without bound', () => {
    // The pane is capped and non-shrinking so the list it was opened from stays
    // on screen. Without the cap, a long JSON payload pushes the rows away.
    const { container } = wrap(
      <DetailPane title="Observation" onClose={() => {}}>
        <div>body</div>
      </DetailPane>,
    );

    const pane = container.firstElementChild as HTMLElement;
    const styles = window.getComputedStyle(pane);
    expect(styles.maxHeight).not.toBe('none');
    expect(styles.flexShrink).toBe('0');
  });
});

describe('a record can fill the panel', () => {
  it('offers a maximise toggle', async () => {
    wrap(
      <DetailPane title="Observation" onClose={() => {}}>
        <div>body</div>
      </DetailPane>,
    );

    await userEvent.click(screen.getByLabelText('Maximise detail pane'));
    expect(screen.getByLabelText('Restore detail pane')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('Restore detail pane'));
    expect(screen.getByLabelText('Maximise detail pane')).toBeInTheDocument();
  });

  it('hides the drag handle while maximised', async () => {
    wrap(
      <DetailPane title="Observation" onClose={() => {}}>
        <div>body</div>
      </DetailPane>,
    );

    expect(screen.getByRole('separator')).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText('Maximise detail pane'));
    // There is no split left to drag.
    expect(screen.queryByRole('separator')).not.toBeInTheDocument();
  });

  it('Escape un-maximises before it closes', async () => {
    const onClose = vi.fn();
    wrap(
      <DetailPane title="Observation" onClose={onClose}>
        <div>body</div>
      </DetailPane>,
    );

    await userEvent.click(screen.getByLabelText('Maximise detail pane'));

    // First Escape steps back to the split view rather than dumping the
    // engineer out of the record they were reading.
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Maximise detail pane')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('the split is draggable', () => {
  beforeEach(() => window.localStorage.clear());

  it('exposes the border as a labelled separator', () => {
    wrap(
      <DetailPane title="Observation" onClose={() => {}}>
        <div>body</div>
      </DetailPane>,
    );

    const handle = screen.getByRole('separator', { name: 'Resize detail pane' });
    expect(handle).toBeInTheDocument();
    expect(handle).toHaveAttribute('aria-orientation', 'horizontal');
    // Focusable, so the split is reachable without a pointer.
    expect(handle).toHaveAttribute('tabindex', '0');
  });

  it('grows on ArrowUp and shrinks on ArrowDown', () => {
    wrap(
      <DetailPane title="Observation" onClose={() => {}} storageKey="test-pane">
        <div>body</div>
      </DetailPane>,
    );

    const handle = screen.getByRole('separator');
    fireEvent.keyDown(handle, { key: 'ArrowUp' });
    const grown = Number(window.localStorage.getItem('vosvc.split.test-pane'));
    expect(grown).toBeGreaterThan(0);

    fireEvent.keyDown(handle, { key: 'ArrowDown' });
    const shrunk = Number(window.localStorage.getItem('vosvc.split.test-pane'));
    expect(shrunk).toBeLessThan(grown);
  });

  it('never shrinks past the floor that keeps the list usable', () => {
    wrap(
      <DetailPane title="Observation" onClose={() => {}} storageKey="test-pane">
        <div>body</div>
      </DetailPane>,
    );

    const handle = screen.getByRole('separator');
    for (let i = 0; i < 40; i += 1) fireEvent.keyDown(handle, { key: 'ArrowDown' });

    // A pane draggable to zero is a pane that vanishes and looks like a bug.
    expect(Number(window.localStorage.getItem('vosvc.split.test-pane'))).toBeGreaterThanOrEqual(120);
  });

  it('resets to the default on double-click', () => {
    wrap(
      <DetailPane title="Observation" onClose={() => {}} storageKey="test-pane">
        <div>body</div>
      </DetailPane>,
    );

    const handle = screen.getByRole('separator');
    fireEvent.keyDown(handle, { key: 'ArrowUp' });
    expect(window.localStorage.getItem('vosvc.split.test-pane')).not.toBeNull();

    fireEvent.doubleClick(handle);
    expect(window.localStorage.getItem('vosvc.split.test-pane')).toBeNull();
  });

  it('remembers the split per panel, not globally', () => {
    window.localStorage.setItem('vosvc.split.observations', '400');
    window.localStorage.setItem('vosvc.split.vision-state', '200');

    const { unmount } = wrap(
      <DetailPane title="A" onClose={() => {}} storageKey="observations">
        <div>a</div>
      </DetailPane>,
    );
    expect(screen.getByText('A').closest('div')?.parentElement).toBeTruthy();
    unmount();

    // Two panels holding different splits must not overwrite one another — an
    // engineer sizing the Observations inspector should not resize Tracks.
    expect(window.localStorage.getItem('vosvc.split.observations')).toBe('400');
    expect(window.localStorage.getItem('vosvc.split.vision-state')).toBe('200');
  });

  it('works without a storage key, just without memory', () => {
    wrap(
      <DetailPane title="Observation" onClose={() => {}}>
        <div>body</div>
      </DetailPane>,
    );

    const handle = screen.getByRole('separator');
    fireEvent.keyDown(handle, { key: 'ArrowUp' });
    expect(window.localStorage.length).toBe(0);
  });

  it('can be turned off entirely', () => {
    wrap(
      <DetailPane title="Observation" onClose={() => {}} resizable={false}>
        <div>body</div>
      </DetailPane>,
    );
    expect(screen.queryByRole('separator')).not.toBeInTheDocument();
  });
});
