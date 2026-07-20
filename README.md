# AI Intel Dashboard Redirect

This repo hosts a static redirect page that always points to the latest
deployed version of the AI Intel Dashboard.

The redirect target is in the `<meta http-equiv="refresh">` tag in `index.html`.

## How to update

When Maxie deploys a new dashboard, the `index.html` is updated to point to the
new URL and pushed to this repo's `main` branch. GitHub Pages automatically
serves the latest commit.

## Bookmark the URL

The URL never changes:

- https://bubbagump86.github.io/ai-dashboard/
