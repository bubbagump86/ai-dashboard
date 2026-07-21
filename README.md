# AI Intel Dashboard

Strategic intelligence on 40 of the most important AI companies in 2026.

- Live site: https://bubbagump86.github.io/ai-dashboard/
- Data file: data.json (auto-refreshed daily)
- Source: [Minimax private workspace]

## Update workflow
1. refresh.py pulls latest news from public RSS feeds
2. funding detection (10% threshold, no churn)
3. writes new data.json
4. git push → GitHub Pages redeploys in ~30s
5. users see new data on next page refresh

No HTML rebuild, no CDN redeploy, no URL change.
