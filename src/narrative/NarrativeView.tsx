/**
 * Narrative View — the sentence, plus proof that it is only a sentence.
 *
 * Prose is persuasive in a way JSON is not, and that is exactly the danger: a
 * reader trusts a fluent sentence more than it has earned. So this view ships
 * its own audit alongside the text:
 *
 * - **every clause names the field it came from** (hover, or read the table)
 * - **fields the narrative did not speak are listed**, so nothing is hidden by
 *   sounding complete
 * - **caveats are separated from the sentence**, because "this score is
 *   uncalibrated" is a statement about how to read the record, not a claim
 *   about the world
 *
 * The switch between Raw JSON, Structured and Narrative renders **the same
 * record** three ways. If they ever disagree, the JSON is right.
 */

import { useMemo, useState } from 'react';
import {
  Box,
  Chip,
  Divider,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import type { Observation } from '@contract/types';
import { JsonView, Mono } from '@components/primitives';
import { mono, semantic } from '@/theme/theme';
import { renderObservation, type Clause } from './render';

export type ObservationLens = 'json' | 'structured' | 'narrative';

export function NarrativeView({
  observation,
  anchorNs = 0,
}: {
  observation: Observation;
  anchorNs?: number;
}) {
  const narrative = useMemo(
    () => renderObservation(observation, { anchorNs }),
    [observation, anchorNs],
  );
  const [hovered, setHovered] = useState<string | null>(null);

  return (
    <Stack spacing={1.5}>
      <Paper elevation={0} sx={{ p: 1.5, borderLeft: `3px solid ${semantic.injected}` }}>
        <Typography sx={{ fontSize: '0.95rem', lineHeight: 1.75 }}>
          {narrative.clauses
            .filter((c) => c.kind !== 'caveat')
            .map((clause, index) => (
              <ClauseSpan
                key={index}
                clause={clause}
                dim={hovered !== null && !clause.fields.includes(hovered)}
                onHover={setHovered}
                isLast={index === narrative.clauses.filter((c) => c.kind !== 'caveat').length - 1}
              />
            ))}
        </Typography>
      </Paper>

      {narrative.caveats.length > 0 ? (
        <Box>
          <Typography variant="h3" sx={{ mb: 0.5 }}>
            How to read this
          </Typography>
          <Stack spacing={0.5}>
            {narrative.caveats.map((caveat) => (
              <Typography
                key={caveat}
                variant="caption"
                sx={{
                  display: 'block',
                  pl: 1,
                  borderLeft: `2px solid ${semantic.degraded}`,
                  color: 'text.secondary',
                }}
              >
                {caveat}
              </Typography>
            ))}
          </Stack>
          <Typography variant="caption" sx={{ display: 'block', mt: 0.75, fontStyle: 'italic' }}>
            These describe how the record must be read. None of them is a claim about the scene.
          </Typography>
        </Box>
      ) : null}

      <Divider />

      <Box>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.75 }}>
          <Typography variant="h3">Clause → field</Typography>
          <Chip size="small" label={`${narrative.clauses.length} clauses`} />
          <Tooltip
            arrow
            title="Every word above was either a fixed template or a value copied from one of these fields. Nothing was inferred."
          >
            <Chip
              size="small"
              label="traceable"
              sx={{ color: semantic.observing, border: `1px solid ${semantic.observing}` }}
            />
          </Tooltip>
        </Stack>

        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={{ width: 90 }}>kind</TableCell>
              <TableCell>clause</TableCell>
              <TableCell sx={{ width: 220 }}>source field(s)</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {narrative.clauses.map((clause, index) => (
              <TableRow
                key={index}
                hover
                onMouseEnter={() => setHovered(clause.fields[0] ?? null)}
                onMouseLeave={() => setHovered(null)}
              >
                <TableCell>
                  <Mono colour={semantic.unknown}>{clause.kind}</Mono>
                </TableCell>
                <TableCell>
                  <Mono>{clause.text}</Mono>
                </TableCell>
                <TableCell>
                  <Mono colour={semantic.injected}>{clause.fields.join(', ')}</Mono>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>

      {narrative.unrenderedFields.length > 0 ? (
        <Box>
          <Typography variant="h3" sx={{ mb: 0.5 }}>
            Present on the record, not spoken
          </Typography>
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
            {narrative.unrenderedFields.map((field) => (
              <Chip key={field} size="small" label={field} variant="outlined" />
            ))}
          </Stack>
          <Typography variant="caption" sx={{ display: 'block', mt: 0.75 }}>
            Listed so the sentence cannot sound complete while omitting something. Switch to Raw
            JSON to read them.
          </Typography>
        </Box>
      ) : null}
    </Stack>
  );
}

function ClauseSpan({
  clause,
  dim,
  onHover,
  isLast,
}: {
  clause: Clause;
  dim: boolean;
  onHover: (field: string | null) => void;
  isLast: boolean;
}) {
  const colour =
    clause.kind === 'lead' ? 'text.primary' : clause.kind === 'qualifier' ? semantic.unknown : 'text.primary';

  return (
    <Tooltip arrow placement="top" title={`from: ${clause.fields.join(', ')}`}>
      <Box
        component="span"
        onMouseEnter={() => onHover(clause.fields[0] ?? null)}
        onMouseLeave={() => onHover(null)}
        sx={{
          color: colour,
          opacity: dim ? 0.32 : 1,
          transition: 'opacity 120ms',
          borderBottom: '1px dotted',
          borderColor: 'divider',
          cursor: 'help',
          fontWeight: clause.kind === 'lead' ? 600 : 400,
        }}
      >
        {clause.text}
        {isLast ? '.' : clause.kind === 'qualifier' ? ' · ' : '; '}
      </Box>
    </Tooltip>
  );
}

/**
 * The three-way lens over one record.
 *
 * Raw JSON is the source of truth and is always reachable in one click. The
 * other two are conveniences over it, and the order here says so.
 */
export function ObservationLensView({
  observation,
  lens,
  anchorNs = 0,
}: {
  observation: Observation;
  lens: ObservationLens;
  anchorNs?: number;
}) {
  if (lens === 'json') return <JsonView value={observation} maxHeight={340} />;
  if (lens === 'narrative') return <NarrativeView observation={observation} anchorNs={anchorNs} />;
  return <StructuredView observation={observation} />;
}

/** Field-by-field, grouped. No prose, no interpretation — just an ordered read. */
function StructuredView({ observation }: { observation: Observation }) {
  const record = observation as unknown as Record<string, unknown>;

  const groups: Array<{ title: string; keys: string[] }> = [
    { title: 'Identity', keys: ['observation_id', 'observation_type', 'object_id', 'track_id', 'class_id'] },
    { title: 'Time', keys: ['t_capture_ns', 't_capture_unc_ms', 't_published_ns', 'clock_quality'] },
    { title: 'Where', keys: ['camera_id', 'frame_ref', 'spatial'] },
    { title: 'Claim', keys: ['attributes', 'confidence', 'lifecycle_state', 'lifecycle_transition', 'identity', 'coverage', 'quality', 'measurement_basis'] },
    { title: 'Explainability (V4)', keys: ['provenance', 'evidence_ref', 'lineage', 'supersedes'] },
    { title: 'Bookkeeping', keys: ['tenant_id', 'site_id', 'schema_version', 'taxonomy_version', 'demand_ids', 'labels', 'timing'] },
  ];

  const known = new Set(groups.flatMap((g) => g.keys));
  const extra = Object.keys(record).filter((k) => !known.has(k));
  if (extra.length) groups.push({ title: 'Unrecognised by this console', keys: extra });

  return (
    <Stack spacing={1.25}>
      {groups.map((group) => {
        const rows = group.keys.filter((key) => record[key] !== undefined && record[key] !== null);
        if (!rows.length) return null;
        return (
          <Box key={group.title}>
            <Typography variant="h3" sx={{ mb: 0.5 }}>
              {group.title}
            </Typography>
            <Table size="small">
              <TableBody>
                {rows.map((key) => (
                  <TableRow key={key}>
                    <TableCell sx={{ width: 200, verticalAlign: 'top' }}>
                      <Mono colour={semantic.unknown}>{key}</Mono>
                    </TableCell>
                    <TableCell>
                      <Box
                        component="pre"
                        sx={{ m: 0, fontFamily: mono, fontSize: '0.72rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
                      >
                        {typeof record[key] === 'object'
                          ? JSON.stringify(record[key], null, 2)
                          : String(record[key])}
                      </Box>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        );
      })}
    </Stack>
  );
}
