"""Compute dashboard_stats block for the AI Intel Dashboard.

Reads the data.json file, computes aggregated stats (capital raised, ARR,
valuation, etc.), and returns a dict with the new stats. The caller
decides how to persist / publish it (typically: append to a stats.json
sidecar, or include in the data.json wrapper).

This is called from refresh.py at the end of each run so the
GitHub-Pages-served data.json always has fresh stats. It's also
safe to call from a separate cron.

Public companies (ServiceNow, Snowflake, DataDog, AvePoint) are valued
at their current market cap (funding.current_market_cap_usd), not their
post-money valuation (which is stale and was historically set once at
IPO).
"""
import datetime
import json
from pathlib import Path

DATA_PATH = Path('/workspace/ai-dashboard/dist/data.json')

# Public companies: valued at market cap, not post-money
PUBLIC_COMPANIES = {'ServiceNow', 'Snowflake', 'DataDog', 'AvePoint'}


def compute_stats(data):
    """Compute dashboard_stats from a list of company dicts.

    Returns a dict ready to be published as data['dashboard_stats'] or
    written to a separate stats.json sidecar.
    """
    n = len(data)

    # Capital raised: only sum total_raised_usd, NOT amount_usd
    # (amount_usd is the LATEST ROUND amount, not lifetime)
    capital_total = 0
    capital_disclosed = 0
    latest_round_total = 0
    latest_round_count = 0
    for c in data:
        f = c.get('funding') or {}
        tr = f.get('total_raised_usd')
        if isinstance(tr, (int, float)) and tr > 0:
            capital_total += tr
            capital_disclosed += 1
        amt = f.get('amount_usd')
        if isinstance(amt, (int, float)) and amt > 0:
            latest_round_total += amt
            latest_round_count += 1

    # ARR
    arr_total = 0
    arr_disclosed = 0
    for c in data:
        a = c.get('arr_usd')
        if isinstance(a, (int, float)) and a > 0:
            arr_total += a
            arr_disclosed += 1

    # Valuation: private post-money + public market cap
    private_val = 0
    private_count = 0
    public_val = 0
    public_count = 0
    for c in data:
        f = c.get('funding') or {}
        if c.get('name') in PUBLIC_COMPANIES:
            cap = f.get('current_market_cap_usd')
            if isinstance(cap, (int, float)) and cap > 0:
                public_val += cap
                public_count += 1
        else:
            pm = f.get('post_money_valuation_usd')
            if isinstance(pm, (int, float)) and pm > 0:
                private_val += pm
                private_count += 1

    # Categories + news
    cat_set = set(c.get('category') for c in data if c.get('category'))
    news_total = sum(len(c.get('latest_news') or []) for c in data)

    now = datetime.datetime.now().astimezone()
    return {
        'last_updated': now.isoformat(timespec='seconds'),
        'as_of_date':   now.date().isoformat(),
        'companies_tracked': n,
        'categories':       len(cat_set),
        'news_items':       news_total,
        'capital_raised': {
            'total_usd':              int(capital_total),
            'disclosed_count':        capital_disclosed,
            'total_count':            n,
            'latest_round_total_usd': int(latest_round_total),
            'latest_round_count':     latest_round_count,
            'note': 'sum of funding.total_raised_usd (lifetime)',
        },
        'combined_arr': {
            'total_usd':      int(arr_total),
            'disclosed_count': arr_disclosed,
            'total_count':     n,
            'note': 'sum of arr_usd; only disclosed companies included',
        },
        'combined_valuation': {
            'total_usd':              int(private_val + public_val),
            'private_post_money_usd': int(private_val),
            'private_count':          private_count,
            'public_market_cap_usd':  int(public_val),
            'public_count':           public_count,
            'note': 'private = post_money_valuation_usd; public = current_market_cap_usd',
        },
    }


def load_data(path=DATA_PATH):
    p = Path(path)
    return json.loads(p.read_text())


if __name__ == '__main__':
    data = load_data()
    new = compute_stats(data)
    print(json.dumps(new, indent=2))
