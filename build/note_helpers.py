#!/usr/bin/env python3
"""Safely update the market-cap clause(s) inside a funding source_note.

Why this module exists
----------------------
These notes accumulate market-cap fragments over time, e.g.

    ... Stock ~$16.56 (Nov 10) => **market cap ~$3.48B**. Source: ...
    Stock ~$16.4-16.8 (Nov 10-11) => **market cap ~$3.4B**.

and sometimes the cap sits mid-paragraph:

    ... pulled back to ~$269.54 (Nov 6) ... => **market cap ~$97B**
    (Morningstar/Nasdaq snapshot Nov 6-7, 2026). Next catalyst: ...

Naive regexes corrupt them:
  * `[^.]*` stops at the '.' inside '$16.43', truncating the number.
  * `[\d,.]+` eats a neighbouring decimal point.
  * Walking backwards for a lead-in guesses wrong when the cap is embedded
    in a long paragraph.

Algorithm (deliberately conservative)
--------------------------------------
For each occurrence of the literal marker 'market cap ~$':
  1. The *value* runs from the marker to the end of its numeric token.
  2. The clause *body* runs from the marker to the end of the sentence,
     scanning with parenthesis awareness so a ';' inside '(Nov 11; $295.86
     close)' does not terminate it early.
  3. The clause *lead-in* is whatever immediately precedes the marker back
     to the nearest boundary that is NOT part of a sentence — i.e. we only
     eat a lead-in when it is short and clearly a lead-in ('Stock ~$X
     (date) =>').
  4. Everything after the clause is preserved verbatim, including
     trailing parentheticals such as '(Morningstar/Nasdaq snapshot...)'.

Then exactly one fresh clause is appended.
"""
import re

MARKER = 'market cap ~$'
_VALUE_RE = re.compile(r'\$\s*[\d,.]+\s*(?:B|M|bn|mn|billion|million)?', re.IGNORECASE)
# A short, self-contained lead-in: 'Stock ~$292.79 (Nov 10, +0.56%) =>'
# The lead-in may start mid-paragraph, so we allow any boundary that is
# not inside a word.
#
# NOTE: the body pattern must NOT use '[^.]' because '$292.79' contains a
# decimal point. Instead we allow '.' only when it is not followed by
# whitespace, so a decimal survives but a sentence boundary does not.
_LEADIN_BODY = r'(?:\.(?!\s)|[^.;]){0,110}?'
_LEADIN_RE = re.compile(
    r'(?:^|(?<=[\s.;]))'
    r'((?:Stock|Shares|stock|shares|Market cap|market cap|Trading|trading)\b'
    + _LEADIN_BODY + r'(?:=>|⇒)\s*\*{0,2})$'
)
# Lead-ins whose body contains a ';' inside parentheses, e.g.
# 'Stock ~$291.14 (Nov 11; $295.86 Nov 10 close) =>'. The semicolon is a
# date separator, not a clause break, so we handle it with a dedicated
# parenthesis-aware pattern.
_LEADIN_PAREN_RE = re.compile(
    r'(?:^|(?<=[\s.;]))'
    r'((?:Stock|Shares|stock|shares|Market cap|market cap|Trading|trading)\b'
    r'(?:[^.;]|\.(?!\s)|\([^)]*\)){0,160}?(?:=>|⇒)\s*\*{0,2})$'
)


def _value_end(text, marker_idx):
    """End index (exclusive) of the numeric value that follows the marker."""
    rest = text[marker_idx + len(MARKER):]
    m = _VALUE_RE.match(rest)
    if not m:
        # No clean number (e.g. '~$97B'); consume a conservative token run.
        m = re.match(r'[^\s*;,.]{1,20}', rest)
        if not m:
            return marker_idx + len(MARKER)
    return marker_idx + len(MARKER) + m.end()


def _sentence_end(text, start):
    """Index just past the end of the sentence containing `start`.

    Parenthesis-aware: a ';' or '.' inside '(...)' does not end the
    sentence.
    """
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        elif depth == 0:
            if ch in '.;!' and (i + 1 >= len(text) or text[i + 1].isspace()):
                return i + 1
            if ch == '\n':
                return i + 1
        i += 1
    return len(text)


def _strip_one(note):
    """Remove the first market-cap clause. Returns (new_note, removed)."""
    i = note.find(MARKER)
    if i == -1:
        return note, None

    val_end = _value_end(note, i)
    # The bold run may wrap the value: '**market cap ~$3.48B**'.
    # Absorb the trailing '**' and, if the opening '**' sits immediately
    # before the marker, absorb that too.
    marker_end = i + len(MARKER)
    if not note[marker_end:val_end].endswith('**') and note[val_end:val_end + 2] == '**':
        val_end += 2
    open_start = i
    if note[:i].endswith('**') and not note[:i - 2].endswith('**'):
        open_start = i - 2
    sent_end = _sentence_end(note, val_end)

    # Consume a short lead-in immediately before the marker.
    head = note[:open_start]
    start = open_start
    for rex in (_LEADIN_PAREN_RE, _LEADIN_RE):
        m = rex.search(head)
        if m:
            start = m.start(1)
            break

    removed = note[start:sent_end]
    return note[:start] + note[sent_end:], removed


def strip_market_cap_clauses(note):
    """Remove every market-cap clause from `note`."""
    if not note:
        return note
    while MARKER in note:
        new_note, removed = _strip_one(note)
        if removed is None or new_note == note:
            break
        note = new_note
    return note


def update_market_cap_note(note, new_cap_usd, new_line=None):
    """Replace every market-cap clause in `note` with a single fresh one."""
    if note is None:
        return note
    if new_line is None:
        new_line = f'Stock ⇒ **market cap ~${new_cap_usd/1e9:.2f}B**.'

    base = strip_market_cap_clauses(note)
    base = re.sub(r'\s{2,}', ' ', base)
    base = re.sub(r'\s+([.;,])', r'\1', base)
    base = re.sub(r'\(\s*\)', '', base)
    base = base.rstrip()
    if base and not base.endswith(('.', '!', '?')):
        base += '.'
    return (base + ' ' + new_line).strip()


# ---------------------------------------------------------------------------
# Self-test with the real production notes (all four public companies).
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    CASES = [
        # (name, note, cap, new_line, must_not_contain)
        ('Snowflake',
         'Public (NYSE: SNOW). FY2027 Q2: revenue $1.55B (+35.1% YoY). Stock +32.5% YTD '
         'but pulled back to ~$269.54-276.44 (Nov 6) from $306.08 (Oct 30) '
         '(52-week range $118.30-$384.56) => **market cap ~$97B** '
         '(Morningstar/Nasdaq snapshot Nov 6-7, 2026). Next catalyst: FY2027 Q3 '
         'earnings Dec 2, 2026. Source: Yahoo Finance (Sept 2 - Nov 4, 2026). '
         'Stock rebounded to ~$292.79 (Nov 10, +0.56%) => **market cap ~$103.3B**. '
         'Stock ~$291.14 (Nov 11; $295.86 Nov 10 close) => **market cap ~$102.9B**.',
         104_800_000_000,
         'Stock ~$297.07 (Nov 12, -1.38%) => **market cap ~$104.8B**; revenue LTM $5.43B.',
         ['$97B', '$103.3B', '$102.9B']),
        ('AvePoint',
         'Public (NASDAQ: AVPT). **Nov 10: Morgan Stanley downgraded Overweight -> '
         'Equal Weight**; consensus Moderate Buy, average target $18.00. '
         'Stock ~$16.56 (Nov 10) => **market cap ~$3.48B**. '
         'Source: SEC/GlobeNewswire (Nov 5-10, 2026). '
         'Stock ~$16.4-16.8 (Nov 10-11) => **market cap ~$3.4B**.',
         3_500_000_000,
         'Stock ~$16.43 (Nov 12, -0.67%; 210.08M shares) => **market cap ~$3.5B**.',
         ['$3.48B', '$3.4B**']),
        ('ServiceNow',
         'Public (NYSE: NOW). Shares 1.047B; stock ~$108.75 (Nov 5) => '
         '**market cap ~$112.9B**. Buyback: additional $5B authorised Jan 2026. '
         'Source: CTech.',
         104_600_000_000,
         'Stock ~$100.82 (Nov 12 close, -2.20%) => **market cap ~$104.6B**; 1.04B shares.',
         ['$112.9B']),
        ('DataDog',
         'Public (NASDAQ: DDOG). Q2 beat. Stock ~$174.93 (Nov 10, -2.61%) => '
         '**market cap ~$63.2B**. Source: Nasdaq (Nov 10). '
         'Stock ~$178.40 (Nov 10 close, -0.68%) => **market cap ~$64.3B**.',
         66_200_000_000,
         'Stock ~$183.45 (Nov 12, -0.50%) => **market cap ~$66.2B**.',
         ['$63.2B', '$64.3B']),
    ]

    failures = []
    for name, note, cap, line, forbidden in CASES:
        out = update_market_cap_note(note, cap, line)
        if out.count(MARKER) != 1:
            failures.append(f'{name}: {out.count(MARKER)} cap mentions')
        if '$$' in out:
            failures.append(f'{name}: double dollar')
        if out.count('**') % 2 != 0:
            failures.append(f'{name}: unbalanced bold')
        for bad in forbidden:
            if bad in out:
                failures.append(f'{name}: stale {bad}')
        # Idempotency: a second run must not duplicate or corrupt.
        out2 = update_market_cap_note(out, cap, line)
        if out2.count(MARKER) != 1:
            failures.append(f'{name}: not idempotent')
        print(f'--- {name} ---')
        print(out)
        print()

    if failures:
        print('FAILURES:')
        for f in failures:
            print(' -', f)
        raise SystemExit(1)
    print('ALL SELF-TESTS PASS')
