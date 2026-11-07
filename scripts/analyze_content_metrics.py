#!/usr/bin/env python3
"""Validate normalized content CSV and produce comparable descriptive groups."""
import argparse
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median

REQUIRED = ('content_id', 'platform', 'window_hours', 'distribution', 'content_type', 'views')
COUNTS = ('views', 'likes', 'comments', 'shares', 'follows')
DISTRIBUTIONS = {'organic', 'paid', 'mixed', 'unknown'}


def numeric(value, key, optional=True):
    if value is None or (isinstance(value, str) and not value.strip()):
        if optional:
            return None
        raise ValueError(f'{key}: missing value')
    if isinstance(value, bool):
        raise ValueError(f'{key}: boolean is not a count')
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f'{key}: invalid numeric value {value!r}; normalize units first') from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f'{key}: expected finite non-negative number')
    if key in COUNTS and number != number.to_integral_value():
        raise ValueError(f'{key}: counts must be whole numbers')
    return int(number) if key in COUNTS else float(number)


def analyze(raw_rows, min_comparable=5):
    if isinstance(min_comparable, bool) or not isinstance(min_comparable, int) or min_comparable < 1:
        raise ValueError('min_comparable must be a positive integer')
    rows, seen, duplicates, issues = [], {}, 0, []
    for line, raw in enumerate(raw_rows, 2):
        missing = [k for k in REQUIRED if k not in raw]
        if missing:
            raise ValueError(f'row {line}: missing columns {missing}')
        row = {}
        for key in ('content_id', 'platform', 'content_type', 'distribution'):
            if not isinstance(raw[key], str) or not raw[key].strip():
                raise ValueError(f'row {line}: {key} is required')
            row[key] = raw[key].strip()
        if row['distribution'] not in DISTRIBUTIONS:
            raise ValueError(f'row {line}: distribution must be organic/paid/mixed/unknown')
        row['window_hours'] = numeric(raw['window_hours'], 'window_hours', False)
        if row['window_hours'] == 0:
            raise ValueError(f'row {line}: window_hours must be positive')
        for key in COUNTS:
            row[key] = numeric(raw.get(key), key)
        rate = numeric(raw.get('completion_rate'), 'completion_rate')
        if rate is not None and rate > 1:
            raise ValueError(f'row {line}: completion_rate is a fraction 0..1, not a percent string')
        row['completion_rate'] = rate
        identity = (row['platform'], row['content_id'], row['window_hours'], row['distribution'])
        if identity in seen:
            if seen[identity] != row:
                raise ValueError(f'row {line}: conflicting snapshots for {identity}; select a documented cutoff, do not sum')
            duplicates += 1
            continue
        seen[identity] = row
        if row['views'] == 0 and any(row[k] not in (None, 0) for k in COUNTS[1:]):
            issues.append({'content_id': row['content_id'], 'issue': 'zero views with positive interactions; verify source scope'})
        rows.append(row)
    grouped = {}
    for row in rows:
        group = tuple(row[k] for k in ('platform', 'window_hours', 'distribution', 'content_type'))
        grouped.setdefault(group, []).append(row)
    groups = []
    for key, members in sorted(grouped.items()):
        item = dict(zip(('platform', 'window_hours', 'distribution', 'content_type'), key))
        item['content_ids'] = [r['content_id'] for r in members]
        item['sample_count'] = len(members)
        item['metrics'] = {}
        for metric in (*COUNTS, 'completion_rate'):
            known = [r[metric] for r in members if r[metric] is not None]
            item['metrics'][metric] = {'known_count': len(known), 'missing_count': len(members)-len(known),
                'median': median(known) if known else None,
                'min': min(known) if known else None, 'max': max(known) if known else None}
        valid = item['metrics']['views']['known_count']
        # A minimum is a workflow reporting threshold, not statistical significance.
        item['reporting_state'] = 'sample_insufficient' if valid < min_comparable else 'descriptive_group_only'
        item['comparable_view_samples'] = valid
        item['interaction_rates'] = {}
        for metric in COUNTS[1:]:
            pairs = [(r[metric], r['views']) for r in members if r[metric] is not None and r['views'] not in (None, 0)]
            item['interaction_rates'][metric] = {
                'paired_samples': len(pairs),
                'median_per_content_rate': median(n/d for n,d in pairs) if pairs else None,
                'ratio_of_paired_totals': sum(n for n,d in pairs)/sum(d for n,d in pairs) if pairs else None}
        groups.append(item)
    return {'input_rows': len(raw_rows), 'unique_rows': len(rows), 'identical_duplicates_removed': duplicates,
        'min_comparable_for_reporting': min_comparable, 'groups': groups, 'source_issues': issues,
        'causal_claims_supported': False,
        'notes': ['No cross-platform or cross-window totals. Missing is not zero.',
                  'Each content_id is counted once per platform/window/distribution.',
                  'Rates use explicit denominators; completion rates are not averaged as counts.',
                  'Minimum reporting sample count does not establish significance or business impact.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Normalized UTF-8 CSV; no platform login or uploads')
    parser.add_argument('--min-comparable', type=int, default=5)
    args = parser.parse_args()
    try:
        with args.input.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        if not rows:
            raise ValueError('CSV has no data rows')
        print(json.dumps(analyze(rows, args.min_comparable), ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, str(exc)+'\n')


if __name__ == '__main__':
    main()
