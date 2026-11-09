"""
Refresh dashboard: pull latest news for each company from public sources,
diff against existing data.json, write back if changes detected.

Sources (all free, no API keys needed):
- Google News RSS (universal - works for any company, any language)
- 36kr (general Chinese tech/business - catches Chinese company news)
- Qbitai (Chinese AI-specific)
- TechCrunch (general AI/startup feed)
- SEC EDGAR full-text search (public US companies - with CIK filter)

Output:
- /workspace/ai-dashboard/dist/data.json (updated)
- Console report of changes

Usage:
  python3 refresh.py             # full refresh, writes data.json if changed
  python3 refresh.py --dry-run  # show what would change, no writes
  python3 refresh.py --verbose  # print every company being checked
"""
import json
import re
import sys
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_PATH = Path('/workspace/ai-dashboard/dist/data.json')

# Local: stats computation. Add build/ to sys.path so we can import
# compute_stats regardless of where this script is invoked from.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from compute_stats import compute_stats as _compute_stats
except ImportError:
    _compute_stats = None  # graceful degradation if file is missing

# Source URLs
GOOGLE_NEWS = 'https://news.google.com/rss/search?q={q}&hl={hl}&gl=US&ceid=US:en'
GNEWS_ZH = 'https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
KR36_FEED = 'https://36kr.com/feed'
QBITAI_FEED = 'https://www.qbitai.com/feed'
TECHCRUNCH_FEED = 'https://techcrunch.com/feed'
SEC_EDGAR_SEARCH = 'https://efts.sec.gov/LATEST/search-index?q={q}&dateRange=custom&startdt={start}&enddt={end}&forms=8-K,10-K,10-Q,20-F'
USER_AGENT = 'Mozilla/5.0 (compatible; AI-Intel-Dashboard/1.0; +mailto:maxieliu07@gmail.com)'

# Time window: last 14 days
NOW = datetime.now(timezone.utc)
START_DT = (NOW - timedelta(days=14)).strftime('%Y-%m-%d')
END_DT = NOW.strftime('%Y-%m-%d')

# SEC EDGAR CIK for public US companies. Used to filter EDGAR results so
# we only get filings actually filed BY the company, not just mentioning it.
COMPANY_CIK = {
    'AvePoint': '0001663056',      # AVPT
    'DataDog': '0001561550',       # DDOG
    'ServiceNow': '0001373715',    # NOW
    'Snowflake': '0001640147',     # SNOW
    'Microsoft Research Asia (MSRA)': '0000789019',  # MSFT (MSRA filings filed under Microsoft)
}

# Per-company config: search queries + which Chinese feed (if any) to also use
COMPANY_CHINESE_FEED = {
    'Moonshot AI': True,
    'Zhipu AI': True,
    'MiniMax': True,
    'StepFun': True,
    'Manus AI': True,
    'Sakana AI': True,   # Japanese, but Chinese sources also cover
    'Preferred Networks': True,
    'Alibaba': True,     # (if added later)
    'ByteDance': True,
    'Tencent': True,
}

# Per-company search terms. Multiple variants tried.
COMPANY_QUERIES = {
    'Anthropic': ['Anthropic', 'Claude AI'],
    'OpenAI': ['OpenAI', 'ChatGPT'],
    'Cursor / Anysphere': ['Anysphere', 'Cursor AI coding'],
    'ElevenLabs': ['ElevenLabs'],
    'xAI': ['xAI', 'Grok AI', 'Elon Musk AI'],
    'MiniMax': ['MiniMax', 'Hailuo AI'],
    'A21 Labs': ['AI21', 'Jamba model'],
    'Advanced Machine Intelligence (AMI)': ['Yann LeCun AMI', 'Advanced Machine Intelligence'],
    'AvePoint': ['AvePoint', 'AVPT'],
    'BeyondMath': ['BeyondMath', 'generative physics'],
    'Bottlecap AI': ['Bottlecap AI', '20VC Bottlecap'],
    'Cantina AI': ['Cantina AI'],
    'Causal Labs': ['Causal Labs'],
    'Cognition AI': ['Cognition AI', 'Devin AI coding'],
    'Cohere': ['Cohere AI', 'Cohere enterprise'],
    'Databricks': ['Databricks', 'Mosaic AI'],
    'DataDog': ['Datadog', 'DDOG earnings'],
    'Decart': ['Decart', 'Decart AI world model'],
    'Deepgram': ['Deepgram', 'Aura Nova voice'],
    'Enigma AI Labs': ['Enigma AI Labs', 'Enigma robotics Israel'],
    'Google DeepMind': ['DeepMind', 'Gemini model'],
    'Luma AI': ['Luma AI', 'Dream Machine'],
    'Manus AI': ['Manus AI', 'Manus agent'],
    'Microsoft Research Asia (MSRA)': ['Microsoft Research Asia'],
    'Mistral AI': ['Mistral AI', 'Mistral Large'],
    'Moonshot AI': ['Moonshot AI', 'Kimi model'],
    'Openmind Research Institute': ['Openmind Research', 'Richard Sutton'],
    'Periodic Labs': ['Periodic Labs', 'AI scientist'],
    'Preferred Networks': ['Preferred Networks', 'PLaMo'],
    'Prometheus': ['Project Prometheus Bezos', 'Prometheus AI'],
    'Safe Superintelligence': ['Safe Superintelligence', 'SSI Sutskever'],
    'Sakana AI': ['Sakana AI'],
    'SandboxAQ': ['SandboxAQ', 'Large Quantitative Model'],
    'ServiceNow': ['ServiceNow', 'Now Assist AI'],
    'Snowflake': ['Snowflake', 'Cortex AI'],
    'StepFun': ['StepFun', 'Step 3 model'],
    'Thinking Machine Labs': ['Thinking Machine Labs', 'Murati Tinker'],
    'Wonderful AI': ['Wonderful AI agent'],
    'Workato': ['Workato ONE', 'enterprise MCP'],
    'Zhipu AI': ['Zhipu AI', 'GLM model'],
}

# Cache all feeds (one fetch per run)
_FEED_CACHE = {}
_EDGAR_CACHE = {}


def fetch_feed(url, label=''):
    """Fetch and parse an RSS feed. Returns list of {title, link, pubDate, source}."""
    if url in _FEED_CACHE:
        return _FEED_CACHE[url]
    # Short-circuit if Google News has been failing for this run
    if 'news.google.com' in url and _FEED_CACHE.get('__google_news_disabled__'):
        return []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=5) as resp:
            xml_text = resp.read().decode('utf-8', errors='replace')
        root = ET.fromstring(xml_text)
        items = []
        for item in root.findall('.//item')[:60]:  # cap
            title = (item.findtext('title') or '').strip()
            link = (item.findtext('link') or '').strip()
            pub = (item.findtext('pubDate') or '').strip()
            src_el = item.find('source')
            src = src_el.text.strip() if src_el is not None and src_el.text else label
            items.append({'title': title, 'link': link, 'pubDate': pub, 'source_name': src})
        _FEED_CACHE[url] = items
        return items
    except Exception as e:
        print(f'  ⚠ Feed fetch failed ({label}): {e}', file=sys.stderr)
        _FEED_CACHE[url] = []
        # If Google News is consistently failing, disable it for the rest of this run
        if 'news.google.com' in url:
            _FEED_CACHE.setdefault('__google_news_failures__', 0)
            _FEED_CACHE['__google_news_failures__'] += 1
            if _FEED_CACHE['__google_news_failures__'] >= 3:
                print(f'  ℹ Google News disabled (3 consecutive failures)', file=sys.stderr)
                _FEED_CACHE['__google_news_disabled__'] = True
        return []


def fetch_google_news(query, lang='en'):
    """Google News RSS for a query. Universal source for any company."""
    if lang == 'zh':
        url = GNEWS_ZH.format(q=urllib.parse.quote(query))
    else:
        url = GOOGLE_NEWS.format(q=urllib.parse.quote(query), hl='en-US')
    return fetch_feed(url, label=f'Google News ({lang})')


def fetch_techcrunch():
    """Fetch and parse the TechCrunch feed. Returns list of {title, link, pubDate, source_name}."""
    return fetch_feed(TECHCRUNCH_FEED, label='TechCrunch')


def fetch_sec(query, cik=None):
    """SEC EDGAR full-text search. Returns list of recent filings matching query.
    If CIK is provided, only returns filings from that company (avoids false positives)."""
    cache_key = (query, cik)
    if cache_key in _EDGAR_CACHE:
        return _EDGAR_CACHE[cache_key]
    try:
        url = SEC_EDGAR_SEARCH.format(
            q=urllib.parse.quote(f'"{query}"'),
            start=START_DT,
            end=END_DT,
        )
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        hits = data.get('hits', {}).get('hits', [])
        results = []
        for h in hits[:10]:
            src = h.get('_source', {})
            ciks = src.get('ciks', [])
            # If a CIK filter is set, only accept filings from that company
            if cik and not any(cik in str(c) for c in ciks):
                continue
            results.append({
                'title': f"{src.get('display_names', [''])[0] or query} - {src.get('form', '')} filing",
                'link': f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ciks[0] if ciks else ''}&type={src.get('form', '')}",
                'pubDate': src.get('file_date', ''),
            })
        _EDGAR_CACHE[cache_key] = results
        return results
    except Exception as e:
        print(f'  ⚠ SEC fetch failed for {query!r}: {e}', file=sys.stderr)
        _EDGAR_CACHE[cache_key] = []
        return []


def matches_company(title, company_name, queries):
    """Check if a title mentions any of the company search terms.
    Uses word-boundary matching to avoid false positives like 'OpenAI' matching 'OpenAI-3' or 'aOpen'."""
    title_lower = title.lower()
    for q in queries:
        q_lower = q.lower()
        # Word-boundary match
        if re.search(r'\b' + re.escape(q_lower) + r'\b', title_lower):
            return True
    return False


def parse_pub_date(s):
    """Parse various date formats to YYYY-MM-DD."""
    if not s:
        return ''
    s = s.strip()
    # ISO date
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    # RFC 2822
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    return s[:10]


# Regex patterns for funding round detection
FUNDING_PATTERNS = [
    # "raises / raised / raises / secures / closes / nabs / lands / targets / eyes / bags"
    # (uses $X bn/m OR $X billion/million)
    (re.compile(r'(?:raise|raised|raises|secures|closes|nabs|lands|bags|raises?)\s*\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b', re.I), 'amount'),
    # "$X billion/million raises" - amount before verb (tight gap, no 'valuation' in between)
    (re.compile(r'\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b(?![^.]*\bvaluation\b)\s+(?:to|of|for|in|on)?\s*(?:a|an|the)?\s*(?:new\s+)?(raise|raised|raises|funding|round|investment|extension)\b', re.I), 'amount'),
    # "$300M Seed Round" / "$500M Series D" - amount followed by a round
    # name with an optional qualifier in between ("Seed", "Series D",
    # "Series D extension", "pre-seed").
    (re.compile(
        r'\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b(?![^.]*\bvaluation\b)\s+'
        r'(?:pre[-\s]?seed|seed|series\s+[a-h])\b',
        re.I), 'amount'),
    # "valuation of/at/reaches/hits/targets $X billion" or "$X billion valuation"
    (re.compile(r'(?:valuation|valued)\s*(?:of|at|reaches|hits|targets|to)?\s*\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b', re.I), 'valuation'),
    # "eyes / targets $X billion/million valuation"
    (re.compile(r'(?:eyes|targets|in\s+talks\s+on(?:\s+a)?)\s*\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b', re.I), 'valuation'),
    # "$X billion valuation" (valuation AFTER the amount)
    (re.compile(r'\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b\s+(?:at\s+)?(?:a|an)?\s*valuation\b', re.I), 'valuation'),
    # "valuation ... at/of $X billion"
    (re.compile(r'valuation\s+(?:of|at|near|around|approximately|~)\s*\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b', re.I), 'valuation'),
    # "$30 billion at $1.4 trillion valuation" — the AMOUNT raised, then a
    # separate VALUATION figure. They use different units, so this must be
    # matched as a unit pair rather than by the single-unit patterns above.
    # Unit suffix "T" (trillion) is allowed alongside "trillion".
    (re.compile(
        r'\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b[^.]{0,40}?\$\s*([\d.]+)\s*(trillion|tn|t)\b[^.]{0,20}?\bvaluation\b',
        re.I), 'amount_and_valuation'),
    # "raises $X at a $Y trillion valuation" — verb-first form of the above.
    (re.compile(
        r'(?:raise[sd]?|secures?|closes?|lands?)\s*\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b[^.]{0,30}?at\s+(?:a\s+|an\s+)?\$\s*([\d.]+)\s*(trillion|tn|t)\b[^.]{0,15}?\bvaluation\b',
        re.I), 'amount_and_valuation'),
    # "$30B raise at $1.25T valuation" — short unit suffixes (B/T) instead
    # of the spelled-out words.
    (re.compile(
        r'\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b[^.]{0,30}?\$\s*([\d.]+)\s*(trillion|tn|t)\b[^.]{0,20}?\bvaluation\b',
        re.I), 'amount_and_valuation'),
    # "Eyes $5 Billion at $50 Billion Valuation" / "in talks on a $1.7 billion
    # round at $13 billion valuation" — the FIRST figure is the amount being
    # raised, the SECOND is the valuation. These used to be mis-tagged as
    # 'valuation' for both, so the amount was silently dropped.
    (re.compile(
        r'(?:eyes|targets|in\s+talks\s+on(?:\s+a)?|weighing\s+(?:a\s+)?|mulling\s+(?:a\s+)?|seeking\s+(?:a\s+)?)'
        r'\s*\$\s*([\d.]+)\s*(billion|million|bn|b|m)\b[^.]{0,40}?at\s+'
        r'(?:a\s+|an\s+)?\$\s*([\d.]+)\s*(trillion|billion|million)\b[^.]{0,20}?\bvaluation\b',
        re.I), 'amount_and_valuation'),
    # Series letters
    (re.compile(r'\b(Series\s+[A-H](?:\s+round|\s+funding|\s+extension)?)\b', re.I), 'round'),
    # Pre-seed / Seed
    (re.compile(r'\b(pre[-\s]?seed|seed)\s+(?:round|funding|raise|extension)\b', re.I), 'round'),
]


def parse_money_to_usd(num_str, unit):
    """Convert '500 million' / '2.5 billion' to USD integer."""
    try:
        n = float(num_str)
    except (ValueError, TypeError):
        return None
    unit_l = (unit or '').lower()
    if unit_l in ('trillion', 'tn', 't'):
        return int(n * 1_000_000_000_000)
    if unit_l in ('billion', 'bn', 'b'):
        return int(n * 1_000_000_000)
    if unit_l in ('million', 'm'):
        return int(n * 1_000_000)
    return None

# --- Third-party money guards -------------------------------------------
# These catch headlines where a dollar figure attaches to somebody OTHER
# than the company we're tracking. Every pattern here caused a real bad
# update in production, so each one is anchored to its incident.

# "Amazon in talks to buy X at $7B" / "Amazon nears $7B deal for X"
#   -> the $7B is the ACQUISITION PRICE, not X's funding post-money.
_ACQUISITION_MARKERS = [
    'in talks to buy', 'in talks to acquire', 'talks to buy', 'talks to acquire',
    'nears', 'near $', 'emerges as potential buyer', 'potential buyer of',
    'proposed acquisition', 'acquisition could', 'acquisition talks',
    'buy israeli', 'buys israeli', 'deal for', 'bid for', 'takeover of',
    'acquire israeli', 'acquisition of', 'wants to buy', 'looks to buy',
    'offer for', 'buyout of', 'acquisition price',
]

# "SpaceX lands $45B AI deal and moves to merge xAI" / "X moves to merge Y"
#   -> the $45B is the acquirer's deal; Y is the merge target.
_MERGE_MARKERS = [
    'to merge', 'moves to merge', 'merging with', 'merge with',
    'and moves to merge', 'lands $', 'bags $45', 'ai deal',
]

# A third party is the one raising/landing the money.
_THIRD_PARTY_RAISE_MARKERS = [
    'raises $', 'raised $', 'secures $', 'closes $', 'nabs $', 'bags $',
]

# Words that mark the tracked company as the TARGET of the money, not the
# raiser. If our company appears only after one of these, the $ isn't ours.
_TARGET_MARKERS = [
    'to buy', 'to acquire', 'acquisition of', 'acquire', 'buys', 'buy',
    'merge with', 'to merge', 'target of', 'potential buyer',
]


def _company_token_variants(name):
    """Return lowercase tokens that identify the company in a headline."""
    if not name:
        return []
    n = name.lower()
    variants = {n}
    # Drop parentheticals and trailing qualifiers
    if '(' in n:
        variants.add(n.split('(')[0].strip())
    # Common alternate names used in headlines
    aliases = {
        'zhipu ai': ['zhipu', 'z.ai'],
        'data dog': ['datadog'],
        'cognition ai': ['cognition'],
        'advanced machine intelligence (ami)': ['advanced machine intelligence', 'ami labs'],
        'openmind research institute': ['openmind'],
        'preferred networks': ['preferred networks', 'pfn'],
        'thinking machine labs': ['thinking machine labs', 'tml'],
        'periodic labs': ['periodic labs', 'periodic'],
        'sandboxaq': ['sandboxaq', 'sandbox aq'],
        'mistral ai': ['mistral'],
        'moonshot ai': ['moonshot'],
        'minimax': ['minimax'],
        'sakana ai': ['sakana'],
        'enigma ai labs': ['enigma'],
        'a21 labs': ['ai21'],
        'google deepmind': ['deepmind', 'google deepmind'],
        'microsoft research asia (msra)': ['microsoft research asia', 'msra'],
        'cursor / anysphere': ['cursor', 'anysphere'],
        'safe superintelligence': ['safe superintelligence'],
        'xai': ['xai'],
    }
    for key, alts in aliases.items():
        if n == key or n.startswith(key):
            variants.update(alts)
    toks = set()
    for v in variants:
        for t in re.split(r'[^a-z0-9.]+', v):
            if len(t) >= 3 and t not in ('the', 'and', 'for', 'inc', 'llc'):
                toks.add(t)
    return sorted(toks)


def _money_belongs_to_someone_else(text, company_name):
    """True when the dollar figure in `text` clearly belongs to a party
    other than `company_name`.

    Guards against three production incidents:
      * "SpaceX (SPCX) Lands $45 Billion AI Deal And Moves To Merge xAI"
      * "Amazon in Talks to Buy AI Startup Decart for Around $7 Billion"
      * "AIUC Raises $40M Series A ... Signs Cursor, Lovable, Harvey and ElevenLabs"
    """
    t = text.lower()
    toks = _company_token_variants(company_name)
    if not toks:
        return False

    # Find where our company is mentioned.
    positions = []
    for tok in toks:
        for m in re.finditer(r'(?<![a-z0-9])' + re.escape(tok) + r'(?![a-z0-9])', t):
            positions.append(m.start())
    if not positions:
        return False
    first = min(positions)

    # 1) Acquisition language: someone is buying OUR company. The money is
    #    the buyer's price, not our raise.
    if any(mk in t for mk in _ACQUISITION_MARKERS):
        # If our company is the object of the acquisition verb, bail.
        for mk in _TARGET_MARKERS:
            idx = t.find(mk)
            if idx != -1:
                # If our company is mentioned after the acquisition marker,
                # we are the target -> the amount is the acquisition price.
                if any(p > idx for p in positions):
                    return True
        # "Amazon in talks to buy ... at $7 billion" — amount may appear
        # after our name too, but the acquisition marker alone is enough
        # to disqualify a funding-post-money reading.
        if any(p >= first for p in positions):
            return True

    # 2) Merge language: "X lands $45B AI deal and moves to merge Y".
    if any(mk in t for mk in _MERGE_MARKERS):
        # Our company is the merge target if it appears after "merge".
        for mk in ('to merge', 'merging with', 'merge with'):
            idx = t.find(mk)
            if idx != -1 and any(p > idx for p in positions):
                return True
        # "$45 Billion AI Deal" attributed to another company (e.g. ticker
        # in parens like "SpaceX (SPCX)") -> not our raise.
        if re.search(r'\([a-z]{2,6}\)', t) and not re.search(
                r'(?<![a-z0-9])' + re.escape(toks[0]) + r'(?![a-z0-9])', t.split('(')[0]):
            return True

    # 3) Third-party raise: "AIUC Raises $40M Series A ... Signs ... ElevenLabs"
    #    Our company appears only as a customer/partner/participant, after
    #    the raise verb. If our first mention comes AFTER the raise verb and
    #    is preceded by a participant marker, it's not our money.
    orig = text  # keep original casing to spot the raiser's proper name
    for mk in _THIRD_PARTY_RAISE_MARKERS:
        idx = t.find(mk)
        if idx == -1:
            continue
        # Find the other company that owns the raise: a Capitalised word
        # immediately before the verb. Use the ORIGINAL text for casing.
        prefix = orig[:idx].rstrip()
        m = re.search(r'([A-Z][\w.&-]*(?:\s+[A-Z][\w.&-]*)*)\s*$', prefix)
        if not m:
            continue
        raiser = m.group(1)
        raiser_toks = [x for x in re.split(r'\s+', raiser.lower()) if len(x) >= 3]
        if not raiser_toks:
            continue
        # If the raiser IS our company, this is our raise — keep it.
        # Compare against our own token set (not the whole text, which
        # trivially contains the raiser's name).
        our_toks = set(toks)
        if any(rt in our_toks for rt in raiser_toks):
            continue
        # Otherwise the money belongs to a third party. Our company must
        # appear somewhere as a participant (after the raise verb) for the
        # headline to even mention it — and then it isn't our money.
        participant_markers = [
            'signs', 'signing', 'partners', 'partner with', 'partners with',
            'teams with', 'teams up', 'users', 'customers', 'client',
            'partners with', 'and ', ' along with', 'partners with',
            'to audit', 'to build', 'to use', 'onboards',
        ]
        for pm in participant_markers:
            pidx = t.find(pm)
            if pidx != -1 and any(p > pidx for p in positions):
                return True

    return False


def detect_funding_info(text, company_name=None):
    """Given a headline, extract any funding round info.
    Returns dict with any of: amount_usd, valuation_usd, round_name, date, source_url, source_headline.
    Or None if no funding info found.

    `company_name` lets us reject headlines where the money belongs to a
    DIFFERENT company (e.g. "SpaceX lands $45B AI deal ... merge xAI",
    "Amazon in talks to buy Decart at $7B"). Those are acquisition prices
    or a third party's raise, not this company's post-money valuation.
    """
    if not text:
        return None
    if company_name and _money_belongs_to_someone_else(text, company_name):
        return None
    info = {}
    for pattern, kind in FUNDING_PATTERNS:
        m = pattern.search(text)
        if m:
            if kind in ('amount', 'valuation'):
                usd = parse_money_to_usd(m.group(1), m.group(2))
                if usd:
                    info['amount_usd' if kind == 'amount' else 'valuation_usd'] = usd
            elif kind == 'amount_and_valuation':
                # groups: 1=amount num, 2=amount unit, 3=val num, 4=val unit
                amt = parse_money_to_usd(m.group(1), m.group(2))
                val = parse_money_to_usd(m.group(3), m.group(4))
                if amt:
                    info['amount_usd'] = amt
                if val:
                    info['valuation_usd'] = val
            elif kind == 'round':
                info['round_name'] = m.group(1).strip()
    return info if info else None


def is_funding_headline(text):
    """Return True if headline is primarily about a funding round."""
    if not text:
        return False
    text_l = text.lower()
    # Exclude non-funding contexts first
    excludes = ['lawsuit', 'sued', 'antitrust', 'settlement', 'verdict',
                'merger',  # company mergers (not raising capital)
                'valuation bump', 'valuation could', 'valuation may',
                'why the', 'what the', 'how to', 'explains',
                'sues', 'banned', 'ban', 'laws', 'fined', 'penalty',
                'ipo could', 'ipo may', 'ipo might', 'ipo would',
                ]
    if any(e in text_l for e in excludes):
        return False

    # Strong funding signals
    strong = any(w in text_l for w in [
        'raises $', 'raised $', 'secures $', 'closes $', 'lands $',
        'nabs $', 'bags $',  # various "raises X dollars" patterns
        'in talks on a',
        'funding round', 'series a', 'series b', 'series c', 'series d',
        'series e', 'series f', 'series h',
        'seed round', 'pre-seed', 'post-ipo',
        'stock sale', 'tender offer', 'share sale', 'going public',
        'files for ipo', 'files to go public',
        'secures strategic', 'strategic investment', 'strategic investments',
    ])
    if strong:
        return True

    # "working on new $30B raise", "weighing a $10B round", "planning a
    # $5B raise" — the verb is separated from the amount by a word.
    if re.search(r'(working on|weighing|planning|eyeing|targeting|seeking|mulling|considering)\s+(?:a\s+|an\s+|new\s+|another\s+|up\s+to\s+)?\$', text_l):
        if re.search(r'raise|round|funding|valuation|investment|offering|ipo', text_l):
            return True

    # "Launches / opens / unveils / releases / announces" on its own means
    # a product launch, not a raise — BUT only if there's no round signal
    # alongside it. "Periodic Labs Launches with $300M Seed Round" IS a
    # raise, so check for round/raise language before excluding.
    launch_only = ['opens', 'launches', 'unveils', 'releases', 'announces']
    if any(e in text_l for e in launch_only):
        has_round_signal = any(w in text_l for w in [
            'round', 'series a', 'series b', 'series c', 'series d',
            'series e', 'series f', 'seed', 'pre-seed', 'raise', 'raises',
            'raised', 'secures', 'funding', 'investment', 'valuation',
            'valued at',
        ])
        if not has_round_signal:
            return False

    # Weaker signals: requires both a "valuation" word AND a dollar amount
    has_valuation_word = 'valuation' in text_l or 'valued at' in text_l
    has_dollar = '$' in text and any(c.isdigit() for c in text)
    has_action = any(w in text_l for w in [
        'eyes', 'targets', 'in talks', 'valuation of', 'valued at',
        'secures strategic', 'files for',
    ])
    if has_valuation_word and has_dollar and has_action:
        return True
    return False


def find_news_for_company(company, queries, use_chinese=False):
    """Find recent news items for a company. Returns list of {date, headline, summary, source}.

    Sources queried:
    1. Google News (English) - universal, finds anything indexed
    2. Google News (Chinese) - if use_chinese=True, for Chinese/Japanese companies
    3. 36kr - general Chinese tech/business
    4. Qbitai - Chinese AI-specific
    5. TechCrunch - general AI/startup feed
    6. SEC EDGAR - only for public US companies with a known CIK
    """
    items = []
    seen_urls = set()

    def add(item):
        if item['source'] in seen_urls:
            return
        seen_urls.add(item['source'])
        items.append(item)

    # 1) Google News (English) - primary source
    # Skip Google News if it has been failing all session (avoids 80+ timeouts)
    google_news_enabled = _FEED_CACHE.get('__google_news_disabled__') is None
    if google_news_enabled:
        for q in queries:
            for entry in fetch_google_news(q, lang='en'):
                if matches_company(entry['title'], company, queries):
                    add({
                        'date': parse_pub_date(entry['pubDate']),
                        'headline': entry['title'],
                        'summary': entry['title'],
                        'source': entry['link'],
                    })
        # Detect sustained Google News failure and disable for the rest of the run
        if queries and not _FEED_CACHE.get('__google_news_seen_success__'):
            # If we got no items back from any Google News query this run, disable
            any_success = any(v for k, v in _FEED_CACHE.items() if 'news.google.com' in k and v)
            if not any_success:
                print(f'  ℹ Google News is down (503/timeout); skipping for the rest of this run', file=sys.stderr)
                _FEED_CACHE['__google_news_disabled__'] = True

    # 2) Google News (Chinese) - for companies covered in Chinese press
    if use_chinese:
        for q in queries:
            for entry in fetch_google_news(q, lang='zh'):
                if matches_company(entry['title'], company, queries):
                    add({
                        'date': parse_pub_date(entry['pubDate']),
                        'headline': entry['title'],
                        'summary': entry['title'],
                        'source': entry['link'],
                    })

    # 3) 36kr - Chinese general tech
    if use_chinese:
        for entry in fetch_feed(KR36_FEED, label='36kr'):
            if matches_company(entry['title'], company, queries):
                add({
                    'date': parse_pub_date(entry['pubDate']),
                    'headline': entry['title'],
                    'summary': entry['title'],
                    'source': entry['link'],
                })

    # 4) Qbitai - Chinese AI-specific
    for entry in fetch_feed(QBITAI_FEED, label='Qbitai'):
        if matches_company(entry['title'], company, queries):
            add({
                'date': parse_pub_date(entry['pubDate']),
                'headline': entry['title'],
                'summary': entry['title'],
                'source': entry['link'],
            })

    # 5) TechCrunch - English AI/startup
    for entry in fetch_techcrunch():
        if matches_company(entry['title'], company, queries):
            add({
                'date': parse_pub_date(entry['pubDate']),
                'headline': entry['title'],
                'summary': entry['title'] + ' (TechCrunch)',
                'source': entry['link'],
            })

    # 6) SEC EDGAR (only for public US companies with known CIK)
    cik = COMPANY_CIK.get(company)
    if cik:
        for q in queries:
            for entry in fetch_sec(q, cik=cik):
                add({
                    'date': entry['pubDate'],
                    'headline': entry['title'],
                    'summary': f"SEC filing: {entry['title']}",
                    'source': entry['link'],
                })

    return items


def main():
    dry_run = '--dry-run' in sys.argv
    verbose = '--verbose' in sys.argv

    print(f'Loading {DATA_PATH}...')
    companies = json.loads(DATA_PATH.read_text())
    print(f'  {len(companies)} companies')

    changes = 0
    new_companies = []

    for i, c in enumerate(companies):
        name = c.get('name', '')
        if not name or name not in COMPANY_QUERIES:
            continue
        queries = COMPANY_QUERIES[name]
        use_chinese = COMPANY_CHINESE_FEED.get(name, False)
        if verbose:
            print(f'[{i+1}/{len(companies)}] {name} (zh={use_chinese})...')
        new_items = find_news_for_company(name, queries, use_chinese=use_chinese)
        if not new_items:
            if verbose:
                print(f'  no news found')
            continue
        # Filter to only items not already in the company's latest_news
        existing = c.get('latest_news') or []
        existing_urls = {n.get('source') for n in existing}
        existing_titles = {n.get('headline', '').lower() for n in existing}
        fresh = []
        for item in new_items:
            if item['source'] in existing_urls:
                continue
            # Title dedup (case-insensitive prefix)
            title_lc = item['headline'].lower()[:60]
            if any(t.startswith(title_lc) or title_lc.startswith(t[:60]) for t in existing_titles):
                continue
            fresh.append(item)
        if not fresh:
            if verbose:
                print(f'  no new items (skipped {len(new_items)} dups)')
            continue
        # Sort by date desc, keep top 5 new
        fresh.sort(key=lambda x: x.get('date', ''), reverse=True)
        fresh = fresh[:5]
        print(f'  {name}: +{len(fresh)} new items')
        for it in fresh:
            print(f'    [{it["date"]}] {it["headline"][:80]}')
        if not dry_run:
            # Cap at 10 newest per company
            c['latest_news'] = (fresh + existing)[:10]
            changes += 1
            changes += 1

        # Detect funding round info in new headlines
        funding_changes = []
        company_name = c.get('name', '')
        for it in fresh:
            if not is_funding_headline(it['headline']):
                continue
            finfo = detect_funding_info(it['headline'], company_name)
            if not finfo:
                continue
            # Update the company record if newer and a real improvement
            updated = False
            if 'amount_usd' in finfo and finfo['amount_usd'] > 0:
                current = (c.get('funding') or {}).get('amount_usd') or 0
                if finfo['amount_usd'] > current * 1.1:  # only update if at least 10% bigger
                    c.setdefault('funding', {})['amount_usd'] = finfo['amount_usd']
                    updated = True
                    funding_changes.append(f'amount_usd → ${finfo["amount_usd"]/1e6:.0f}M')
            if 'valuation_usd' in finfo and finfo['valuation_usd'] > 0:
                current = (c.get('funding') or {}).get('post_money_valuation_usd') or 0
                if finfo['valuation_usd'] > current * 1.1:
                    c.setdefault('funding', {})['post_money_valuation_usd'] = finfo['valuation_usd']
                    updated = True
                    funding_changes.append(f'valuation → ${finfo["valuation_usd"]/1e9:.1f}B')
            if 'round_name' in finfo:
                current_round = (c.get('funding') or {}).get('latest_round', '')
                if finfo['round_name'] != current_round:
                    c.setdefault('funding', {})['latest_round'] = finfo['round_name']
                    updated = True
                    funding_changes.append(f'round → {finfo["round_name"]}')
            # Always update date if we have one and it's newer than current
            it_date = it.get('date', '')
            current_date = (c.get('funding') or {}).get('date', '')
            if it_date and (not current_date or it_date > current_date):
                if updated:
                    c['funding']['date'] = it_date
        if funding_changes:
            print(f'    💰 funding updates: {", ".join(funding_changes)}')

    # Update last-updated timestamp
    if not dry_run and changes > 0:
        companies[0]['_refresh_note'] = NOW.strftime('%Y-%m-%d')
        DATA_PATH.write_text(json.dumps(companies, indent=2, ensure_ascii=False))
        print(f'\n✓ Wrote {changes} updated companies to {DATA_PATH}')
    elif dry_run:
        print(f'\n[dry-run] would have updated {changes} companies')
    else:
        print(f'\n✓ No changes detected')

    # Recompute dashboard_stats so the top-line numbers are always fresh
    # alongside news. This runs on every run (even 'no changes' days) so
    # the last_updated timestamp stays current.
    if not dry_run and _compute_stats is not None:
        try:
            stats = _compute_stats(companies)
            stats_path = DATA_PATH.parent / 'stats.json'
            stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False))
            print(f'  📊 Wrote stats.json ({stats["combined_valuation"]["total_usd"]/1e9:.0f}B val, {stats["capital_raised"]["total_usd"]/1e9:.1f}B raised, {stats["combined_arr"]["total_usd"]/1e9:.2f}B ARR)')
        except Exception as e:
            print(f'  ⚠ Failed to write stats.json: {e}')


if __name__ == '__main__':
    main()
