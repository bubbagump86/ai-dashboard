# AI Intel Dashboard — operational notes

Captured run history and hard-won rules. Newest entries at the top.

## Market-cap quoting rules (learned 2026-11-26)

**Always use the regular-session CLOSE price, never an intraday or
after-hours quote.**

On 2026-11-25 the refresh captured Snowflake at an *intraday* $258.78
(12:28 PM) instead of the $267.72 close, understating its market cap by
about $3.2B.

- Compute the cap yourself: `cap = close_price × shares_outstanding`.
- `stockanalysis.com` displays a market cap that reflects its *current*
  (often after-hours) price — do not copy that number straight into the
  dataset.
- Share counts in use: ServiceNow 1,037M · Snowflake 352.80M ·
  DataDog 361.04M · AvePoint 210.08M.
- **US market holidays close the market — but check the calendar
  carefully.** Thanksgiving 2026 fell on Thu Nov 26: US markets were
  **closed Nov 26** but the half-session **did trade** (Nov 25 was the
  last full close, Nov 27 was the first full session back). Do not
  assume "yesterday was a holiday so the print is stale" — confirm by
  reading the close date on the quote page, and cross-check with the
  `/history/` page when the summary page looks stale.
- **The stockanalysis.com summary page can serve a stale intraday read.**
  On 2026-11-27 the summary page for ServiceNow still showed the prior
  session's close while its `/history/` page had the correct Nov 27
  close. When the summary page and a second ticker disagree, trust
  `/history/` and re-read with an explicit "is the most recent close
  Nov 27?" question.
- **Label the note with the actual trading date** of the close you used
  (e.g. "Nov 27 close"), so a reader can tell a stale print from a
  current one.

## Funding semantics

Only a **confirmed, closed round** may update `amount_usd` or
`post_money_valuation_usd`. Everything else is documented in the
`source_note` only.

Guards live in `refresh.py` and are covered by `test_funding_guards.py`:

| Guard | Rejects |
|---|---|
| `_money_belongs_to_someone_else()` | acquisition prices, merge/partner deals, third-party raises |
| `is_unclosed_round_headline()` | "in talks", "seeks", "would raise", "pre-IPO price prediction" |

`is_unclosed_round_headline()` has two important nuances:

- **"working on a round" = under way**, not unclosed. A round may already
  have a confirmed first close (OpenAI's $1.25T in-progress round relies
  on this). The 10% threshold still applies.
- **"Eyes $X at $Y" IS unclosed.** Moonshot's Series G "$50B pre-money
  target" was a proposal; the headline numbers come from its closed
  $35B Series F.

Unclosed rounds are still surfaced as news and printed in the run output
as `⚠ N unclosed round(s) surfaced — headline funding NOT changed:`.

## Feed behaviour

- **Google News** 429s frequently. It auto-disables after 3 consecutive
  failures; the run continues on the remaining feeds.
- **36kr** had a persistent XML parse failure ("not well-formed
  (invalid token): line 10, column 168") for 7 consecutive days through
  Nov 24, then went clean on Nov 25 and Nov 26. If it returns, the
  failure is in the feed payload, not the parser — check the raw
  response before assuming a code bug.
- **SEC EDGAR** returns sporadic HTTP 500s. These are transient and
  server-side; no data is lost because previously-fetched filings stay
  in the dataset.

## Known recurring hazards

- **Do not hand-patch `source_note` with regex.** The text contains
  decimal points inside dollar amounts (`$16.43`, `$3.48B`) and
  semicolons inside parentheses (`(Nov 11; $295.86 close)`). Naive
  patterns truncate numbers or orphan fragments. Use
  `note_helpers.update_market_cap_note()` instead — it is
  parenthesis-aware and number-aware.
- **`sys.path.insert(0, 'build')` is required** before importing
  `compute_stats` from an ad-hoc script, or the import fails and
  `stats.json` silently goes stale.
- **Verify the live site with a direct `curl`**, not a long `sleep`
  chained in front of it — the chained form exceeds the command time
  limit and gets backgrounded.

## Verification checklist before pushing

1. `python3 build/test_funding_guards.py` → all cases pass
2. `python3 build/note_helpers.py` → all self-tests pass
3. Dataset integrity: no `$$`, balanced `**`, exactly one
   `market cap ~$` per public-company note, no dangling `=>.`/`⇒.`,
   intro/product present, ≤10 news items per company
4. Spot-check protected funding values are unchanged
   (Cognition $48B, ElevenLabs $22B, Moonshot $35B, Periodic Labs $7.5B)
5. After push, confirm the live `stats.json` `last_updated` matches
   today and the market caps match
