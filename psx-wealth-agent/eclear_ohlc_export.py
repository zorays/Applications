#!/usr/bin/env python3
"""Normalize copied EClear TradeCast OHLC History grid into auditable OHLCV CSV.

Usage, on Windows: click a cell in EClear's OHLC History grid, Ctrl+A,
Ctrl+C, then run:  py eclear_ohlc_export.py --symbol BNWM --clipboard

Alternatively paste all copied table cells into a UTF-8 text file and run:
  py eclear_ohlc_export.py --symbol BNWM --input BNWM_clipboard.tsv

Never takes screenshots, connects to an account, or places orders.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS = ['date','open','high','low','close','volume']
DATE_FORMATS = ['%b %d, %Y','%B %d, %Y','%Y-%m-%d','%m/%d/%Y','%d/%m/%Y']

def to_date(value):
    value = value.strip().replace('  ', ' ')
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError('unrecognized_date')

def num(value, whole=False):
    x = value.strip().replace(',','').replace(' ','')
    try:
        d = Decimal(x)
    except InvalidOperation:
        raise ValueError('non_numeric_value')
    if not d.is_finite():
        raise ValueError('non_finite_value')
    if whole and (d != d.to_integral_value() or d < 0):
        raise ValueError('bad_volume')
    return int(d) if whole else d

def parse_line(line):
    # Clipboard copy of a Windows DataGridView is tab-separated. Some
    # grid views put a blank row-header column before the actual date.
    parts = next(csv.reader([line], delimiter='\t')) if '\t' in line else None
    if parts is None:
        # Also accept genuine CSV if exported directly from the broker.
        parts = next(csv.reader([line]))
    parts = [s.strip().lstrip('\ufeff') for s in parts]
    if parts and not parts[0]:
        parts = parts[1:]
    if not parts or parts[0].lower() in ('date','row',''):
        return None
    if len(parts) == 7:
        dat, op, hi, lo, cl, change, vol = parts
    elif len(parts) == 6:
        dat, op, hi, lo, cl, vol = parts
    else:
        raise ValueError('expected_6_or_7_columns')
    day = to_date(dat)
    if day.weekday() >= 5:
        raise ValueError('weekend_nontrading_session')
    prices = [num(x) for x in (op,hi,lo,cl)]
    o,h,l,c = prices
    volume = num(vol, True)
    if min(prices) <= 0:
        raise ValueError('zero_or_negative_price')
    if l > min(o,c) or max(o,c) > h or l > h:
        raise ValueError('impossible_ohlc_relationship')
    if day > date.today():
        raise ValueError('future_date')
    return {'date':day.isoformat(), 'open':f'{o:.2f}', 'high':f'{h:.2f}',
            'low':f'{l:.2f}', 'close':f'{c:.2f}', 'volume':volume}

def read_clipboard():
    if __import__('sys').platform != 'win32':
        raise SystemExit('--clipboard requires Windows; use --input instead')
    command = ['powershell.exe','-NoProfile','-Command','Get-Clipboard -Raw']
    result = subprocess.run(command, capture_output=True, text=True, errors='replace', check=True)
    return result.stdout

def export(symbol, text, output_dir):
    output_dir.mkdir(parents=True,exist_ok=True)
    valid = {}
    failures = []
    counted = 0
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        counted += 1
        try:
            rec = parse_line(line)
            if rec is None:
                continue
            prev = valid.get(rec['date'])
            if prev:
                if prev != rec:
                    failures.append({'line':n, 'reason':'conflicting_duplicate_date', 'raw':line})
                    valid.pop(rec['date'],None)
                continue
            valid[rec['date']] = rec
        except ValueError as e:
            failures.append({'line':n,'reason':str(e),'raw':line})
    # A timestamped duplicate conflict can never be silently "unblocked" by
    # a third appearance of the date in a later pasted section.
    conflicts = set()
    for f in failures:
        if f['reason'] == 'conflicting_duplicate_date':
            try:
                conflicts.add(to_date(f['raw'].split('\t')[0]).isoformat())
            except ValueError:
                pass
    for d in conflicts: valid.pop(d,None)
    rows = [valid[d] for d in sorted(valid)]
    out = output_dir / f'{symbol}.eclear.csv'
    with out.open('w',encoding='utf-8',newline='') as f:
        writer = csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    gap_pairs=[]
    for a,b in zip(rows,rows[1:]):
        gap=(date.fromisoformat(b['date'])-date.fromisoformat(a['date'])).days
        if gap > 10:
            gap_pairs.append({'from':a['date'],'to':b['date'],'calendar_days':gap})
    fresh=bool(rows and (date.today()-date.fromisoformat(rows[-1]['date'])).days<=10)
    summary={
        'symbol':symbol,
        'source':'EClear TradeCast OHLC History clipboard table',
        'assurance':'owner_trusted_source; captured_rows_structurally_validated_not_independently_confirmed',
        'rows_valid':len(rows),
        'rows_rejected':len(failures),
        'first_date':rows[0]['date'] if rows else None,
        'last_date':rows[-1]['date'] if rows else None,
        'ready_for_research':len(rows)>=80 and fresh and not gap_pairs and not conflicts,
        'large_calendar_gaps':gap_pairs,
        'csv_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
        'export_generated_at':datetime.now().astimezone().isoformat(),
        'note':'No automatic trading. Check rejected rows and missing sessions before research.'
    }
    meta=output_dir / f'{symbol}.eclear.audit.json'
    meta.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    rejects=output_dir / f'{symbol}.eclear.rejected.json'
    rejects.write_text(json.dumps(failures,indent=2),encoding='utf-8')
    return out,meta,rejects,summary

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbol',required=True)
    src=p.add_mutually_exclusive_group(required=True)
    src.add_argument('--input',type=Path)
    src.add_argument('--clipboard',action='store_true')
    p.add_argument('--output-dir',type=Path,default=Path.cwd())
    args=p.parse_args()
    sym=args.symbol.strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9]{1,11}',sym):
        p.error('invalid PSX symbol')
    raw=read_clipboard() if args.clipboard else args.input.read_text(encoding='utf-8-sig')
    out,meta,rejected,summary=export(sym,raw,args.output_dir)
    print('CSV:',out,'\nAudit:',meta,'\nRejected:',rejected)
    print('Valid:',summary['rows_valid'],'Rejected:',summary['rows_rejected'],
          'Research ready:',summary['ready_for_research'])
    if summary['large_calendar_gaps']:
        print('WARNING: Missing calendar periods:',summary['large_calendar_gaps'])
    if summary['rows_valid']<80:
        print('WARNING: Fewer than 80 sessions; use Ctrl+A/C to copy the entire EClear grid.')

if __name__ == '__main__':
    main()