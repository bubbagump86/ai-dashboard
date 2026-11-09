#!/usr/bin/env python3
"""Regression tests for the funding-detection guards in refresh.py.

Every case in REJECT_CASES is a headline that caused a REAL bad update
in production. Every case in ACCEPT_CASES is a headline that MUST still
produce a funding signal — a guard that blocks these is a regression
that silently stops tracking real raises.

Run:  python3 build/test_funding_guards.py
Exit code 0 = all pass.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from refresh import detect_funding_info  # noqa: E402


# --- Headlines that produced WRONG updates. Guard must reject them. ------
REJECT_CASES = [
    # 2026-11-03: set Decart post_money to $7B (Amazon acquisition price,
    # not a funding round). Re-triggered 2026-11-06 and 2026-11-09.
    ('Decart', 'Amazon in Talks to Buy AI Startup Decart for Around $7 Billion'),
    ('Decart', 'Amazon emerges as potential buyer of Israeli AI startup Decart at $7 billion'),
    ('Decart', 'Amazon nears $7 billion deal for Israeli AI start-up Decart'),
    ('Decart', "Amazon in talks to buy Israel's Decart at $7b valuation - report"),
    ('Decart', 'Why Amazon might pay billions for Decart, a 3-year-old AI startup that hasn\u2019t disclosed its revenue'),
    ('Decart', 'Amazon Stock Price Forecast: Proposed $7 Billion Decart Acquisition Could Push Shares to $300'),

    # 2026-11-06: set xAI amount_usd to $45B — that was SPACEX's deal,
    # not an xAI raise (xAI was acquired by SpaceX in Feb 2026).
    ('xAI', 'SpaceX (SPCX) Lands $45 Billion AI Deal And Moves To Merge xAI'),

    # 2026-09-15: set ElevenLabs latest_round to "Series A" — the raise
    # was AIUC's; ElevenLabs was only listed as a customer.
    ('ElevenLabs', 'AIUC Raises $40M Series A to Audit AI Agents, Signs Cursor, Lovable, Harvey and ElevenLabs'),

    # In-talks raises must not be counted as closed rounds.
    ('OpenAI', 'OpenAI mulls funding round at $1.2 trillion valuation ahead of IPO'),
    ('Anthropic', 'Nvidia Is Weighing a $10 Billion Stake in Anthropic\u2019s IPO'),
    ('Anthropic', 'Anthropic and Nscale in talks for second multibillion-dollar deal'),
    ('Zhipu AI', 'Zhipu AI\'s market capitalization has hit a new low, falling back to HK$300 billion'),
]


# --- Real funding headlines. Guard must NOT block them. -------------------
ACCEPT_CASES = [
    # (company, headline, expected_amount_usd_or_None, expected_valuation_usd_or_None)
    ('Zhipu AI', "China's Zhipu AI raises $4 billion in Hong Kong share sale, source says", 4_000_000_000, None),
    ('Zhipu AI', 'Zhipu AI Secures $5 Billion Financing', 5_000_000_000, None),
    ('Manus AI', 'Manus AI Raises $500M+', 500_000_000, None),
    ('OpenAI', 'OpenAI raises $30 billion at $1.4 trillion valuation', 30_000_000_000, 1_400_000_000_000),
    ('OpenAI', 'OpenAI working on new $30B raise at $1.25T valuation ahead of IPO', 30_000_000_000, 1_250_000_000_000),
    ('Moonshot AI', 'Moonshot AI Eyes $5 Billion at $50 Billion Valuation', 5_000_000_000, 50_000_000_000),
    ('ElevenLabs', 'ElevenLabs hits $22B valuation with employee tender', None, 22_000_000_000),
    ('Decart', 'Decart raises $300M at nearly $4B valuation, led by Radical Ventures', 300_000_000, 4_000_000_000),
    ('Mistral AI', 'Mistral AI secures $600 million in new funding round', 600_000_000, None),
    ('Mistral AI', 'Mistral AI in talks on a $1.7 billion round at $13 billion valuation',
     1_700_000_000, 13_000_000_000),
    ('ElevenLabs', 'ElevenLabs raised $500 million in Series D', 500_000_000, None),
    ('Prometheus', 'Jeff Bezos Prometheus AI startup raises $12 billion Series B', 12_000_000_000, None),
    ('Cursor / Anysphere', 'Anysphere raises $200 million Series E', 200_000_000, None),
    ('Cohere', 'Cohere raises $800 million in Series D', 800_000_000, None),
    ('Sakana AI', 'Sakana AI closes $200 million Series B', 200_000_000, None),
    ('Databricks', 'Databricks raises $5 billion in strategic round', 5_000_000_000, None),
    ('Cognition AI', 'Cognition raises $2 billion Series E', 2_000_000_000, None),
    ('Periodic Labs', 'Periodic Labs Launches with $300M Seed Round', 300_000_000, None),
    ('Enigma AI Labs', 'Enigma Raises $71 Million seed round', 71_000_000, None),
    ('Deepgram', 'Deepgram raises $130 million Series C extension', 130_000_000, None),
    ('Thinking Machine Labs', 'Thinking Machine Labs raises $2 billion seed round', 2_000_000_000, None),
    ('Safe Superintelligence', 'Safe Superintelligence raises $5 billion from Nvidia', 5_000_000_000, None),
    ('StepFun', 'StepFun closes $2.5 billion funding round for HK IPO', 2_500_000_000, None),
]


def main():
    failures = []

    print('=' * 72)
    print('REJECT CASES (guard must block)')
    print('=' * 72)
    for comp, headline in REJECT_CASES:
        info = detect_funding_info(headline, comp)
        if info is None:
            print(f'  PASS  {comp:20} {headline[:60]}')
        else:
            print(f'  FAIL  {comp:20} {headline[:60]}')
            print(f'        -> got {info}')
            failures.append((comp, headline, info))

    print()
    print('=' * 72)
    print('ACCEPT CASES (guard must not block)')
    print('=' * 72)
    for comp, headline, exp_amt, exp_val in ACCEPT_CASES:
        info = detect_funding_info(headline, comp)
        if info is None:
            print(f'  FAIL  {comp:20} {headline[:60]}')
            print(f'        -> nothing detected (regression!)')
            failures.append((comp, headline, 'MISSED'))
            continue
        amt = info.get('amount_usd')
        val = info.get('valuation_usd')
        ok = True
        if exp_amt is not None and amt != exp_amt:
            ok = False
        if exp_val is not None and val != exp_val:
            ok = False
        if ok:
            print(f'  PASS  {comp:20} amt=${(amt or 0)/1e6:.0f}M val=${(val or 0)/1e9:.1f}B')
        else:
            print(f'  FAIL  {comp:20} {headline[:60]}')
            print(f'        -> got amt={amt} val={val}; want amt={exp_amt} val={exp_val}')
            failures.append((comp, headline, (amt, val)))

    print()
    print('=' * 72)
    if failures:
        print(f'{len(failures)} FAILURE(S)')
        for f in failures:
            print(f'  - {f[0]}: {f[1][:70]}')
        return 1
    print(f'ALL {len(REJECT_CASES) + len(ACCEPT_CASES)} CASES PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
