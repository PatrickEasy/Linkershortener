# Linkershortener

A custom-domain link shortener that runs entirely on **GitHub Pages** — no server,
no database, no hosting bill. Short links redirect instantly, can send visitors to a
different destination depending on their country, and can report every click (with
query-string parameters) to a stats endpoint of your choosing.

```
https://patrickeasy.github.io/Linkershortener/amazon
    ->  https://www.amazon.com.au/   (default)
    ->  https://www.amazon.co.uk/    (visitor in GB)
    ->  https://www.amazon.com/      (visitor in US)
```

The same build runs unchanged in three places — the generated pages only use
relative paths, so nothing needs rebuilding to move between them:

| Where                | Base URL                                        | Example short link                                           |
|----------------------|-------------------------------------------------|--------------------------------------------------------------|
| Local preview        | `http://localhost:8000/`                        | `http://localhost:8000/amazon/`                              |
| GitHub project page  | `https://patrickeasy.github.io/Linkershortener/`| `https://patrickeasy.github.io/Linkershortener/amazon`       |
| Custom domain (opt.) | `https://go.example.com/`                       | `https://go.example.com/amazon`                              |

## How it works

GitHub Pages only serves static files, so `shortener.py` is a *generator* rather
than a web app:

1. You manage links with the CLI; they are stored in `links.json`.
2. `python shortener.py build` writes one tiny HTML page per link into
   `docs/<slug>/index.html` (plus a home page, a 404 page, `.nojekyll` and a
   `CNAME` file for your custom domain).
3. You commit `docs/` and push. GitHub Pages serves `docs/` as the site.
4. When a visitor opens `https://go.example.com/<slug>` the page's JavaScript
   reads the query string, optionally looks up the visitor's country, fires a
   click beacon to `STATS_ENDPOINT`, then `location.replace()`s to the destination.
   A `<noscript>` meta-refresh sends JS-less visitors and crawlers to the default URL.

```
Linkershortener/
├── shortener.py        # the CLI / generator
├── links.json          # your links (created on first `add`)
├── links.example.json  # sample showing every supported field
├── .env                # your config (git-ignored) - copy from .env.example
├── .env.example
├── .gitignore
├── requirements.txt    # stdlib only; Logpy is optional
├── stats/
│   └── google_apps_script.gs   # optional ready-made stats backend
└── docs/               # generated site - this is what GitHub Pages serves
    ├── index.html
    ├── 404.html
    ├── CNAME
    ├── .nojekyll
    └── <slug>/index.html
```

## Setup

```bash
cd ~/Documents/GitHub/Linkershortener
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../Logpy_v2         # optional: coloured console output via Logpy
cp .env.example .env               # SITE_URL is preset to the GitHub Pages URL
```

The script has no third-party dependencies; Logpy is used for coloured output when
present and silently skipped otherwise, so a fresh clone works with just the venv.

## Running locally

```bash
python shortener.py add amazon https://www.amazon.com.au/ --geo GB=https://www.amazon.co.uk/
python shortener.py build
python shortener.py serve                 # http://localhost:8000/
```

Then open `http://localhost:8000/amazon/` in a browser. Everything works as it will
in production, including the geo lookup (your browser calls the geo API directly)
and the stats beacon if `STATS_ENDPOINT` is set — so local clicks *will* be counted
unless you leave it blank while testing. The trailing slash on `/amazon/` matters
locally: Python's dev server doesn't redirect `/amazon` → `/amazon/` the way GitHub
Pages does. Use `--port 9000` to pick a different port.

`add` and `list` print links using `SITE_URL`, which is the public address rather
than the local one; that is purely cosmetic and doesn't affect the generated pages.

## Running on GitHub Pages (`https://patrickeasy.github.io/Linkershortener/`)

1. Make sure `.env` has `SITE_URL=https://patrickeasy.github.io/Linkershortener`
   (the default in `.env.example`). With a `github.io` address no `CNAME` file is
   written — GitHub rejects one there.
2. `python shortener.py build`, then commit and push `docs/` and `links.json`.
3. On GitHub: **Settings → Pages → Build and deployment** — Source *Deploy from a
   branch*, Branch `main`, Folder `/docs`. Save. The site is live within a minute
   at `https://patrickeasy.github.io/Linkershortener/` and every link at
   `https://patrickeasy.github.io/Linkershortener/<slug>`.
4. Every change afterwards is just `build` → commit → push.

Because the site lives under the `/Linkershortener/` sub-path, the generator never
writes root-absolute paths like `/amazon/` — every reference in the generated HTML is
relative, which is what lets the same output serve from `localhost:8000/` and
`github.io/Linkershortener/` alike.

## Usage

```bash
# Simple link
python shortener.py add docs https://example.com/documentation

# Random 6-character slug
python shortener.py add https://example.com/some/very/long/url

# Geo-routed link: default AU store, UK visitors -> .co.uk, US visitors -> .com
python shortener.py add amazon https://www.amazon.com.au/ \
    --geo GB=https://www.amazon.co.uk/ \
    --geo US=https://www.amazon.com/

# Forward the query string to the destination
#   https://go.example.com/promo?utm_source=newsletter
#   -> https://example.com/sale?utm_source=newsletter
python shortener.py add promo https://example.com/sale --forward-query --title "Spring sale"

python shortener.py list
python shortener.py remove promo
python shortener.py add amazon https://www.amazon.com.au/ --force   # overwrite

python shortener.py build          # regenerate docs/
python shortener.py serve          # preview at http://localhost:8000/<slug>
python shortener.py stats          # summarise clicks (needs STATS_ENDPOINT)
python shortener.py stats --slug amazon --json
```

Slugs may contain letters, digits, `-` and `_` (max 64 chars) and are case-sensitive
on GitHub Pages.

### Link fields (`links.json`)

| Field           | Required | Meaning                                                             |
|-----------------|----------|---------------------------------------------------------------------|
| `url`           | yes      | Default destination.                                                |
| `geo`           | no       | `{ "GB": "https://...", "US": "https://..." }` — ISO 3166-1 alpha-2 country → URL. |
| `forward_query` | no       | `true` to append the short link's `?query=string` to the destination. |
| `title`         | no       | `<title>` of the redirect page.                                     |
| `note`          | no       | Free text for your own reference.                                   |
| `created`       | auto     | ISO timestamp set by `add`.                                         |

You can edit `links.json` by hand; just run `build` afterwards.

## Optional: a custom domain

Short links get shorter and more on-brand with your own domain (`go.example.com/amazon`
instead of `patrickeasy.github.io/Linkershortener/amazon`):

1. Set `SITE_URL=https://go.example.com` in `.env` and run `build` — this writes
   `docs/CNAME`, which is how GitHub Pages remembers the domain across deploys.
2. At your DNS provider add a `CNAME` record `go` → `patrickeasy.github.io`.
3. **Settings → Pages → Custom domain**: enter `go.example.com`, save, and tick
   *Enforce HTTPS* once the certificate is issued (a few minutes).
4. Commit and push. The `github.io` address keeps working and redirects to the domain.

Switching back is just restoring `SITE_URL` and rebuilding — `build` removes the
`CNAME` file when the URL is a `github.io` one.

### Optional: build in CI instead of committing `docs/`

If you'd rather not commit generated files, add a GitHub Actions workflow that runs
`python shortener.py build` and deploys `docs/` with `actions/upload-pages-artifact` +
`actions/deploy-pages`, and pass `SITE_URL` / `STATS_ENDPOINT` as repository variables
(the script reads real environment variables ahead of `.env`). The script has no
dependencies, so the workflow needs nothing beyond `actions/setup-python`.

## Geo-routing

Because there is no server, the country is detected **in the browser** by calling a
public IP-geolocation API (`GEO_API_URL`, default `https://ipapi.co/json/`). The
page waits at most `GEO_TIMEOUT_MS` (1500 ms) for an answer, then falls back to the
default URL — so a slow or blocked API never strands a visitor. Links without any
`geo` rules skip the lookup entirely and redirect immediately.

Things to know:

- Free tiers are rate-limited (ipapi.co ≈ 1,000 requests/day per IP/origin). For
  serious traffic, sign up for a key and set `GEO_API_URL` to the keyed URL, or swap
  in `https://api.country.is/` / `https://ipwho.is/`. Any API returning JSON with a
  `country_code`, `country` or `countryCode` field works.
- VPNs and ad-blockers may hide the visitor's real country or block the lookup; both
  cases fall through to the default URL.
- If you outgrow client-side detection, the same `links.json` maps neatly onto a
  Cloudflare Worker (`request.cf.country`), which does the routing server-side.

## Statistics

Tracking is off until you set `STATS_ENDPOINT` in `.env` and rebuild. Once set, each
redirect page sends a `navigator.sendBeacon` **POST** (body: JSON, `Content-Type:
text/plain` so no CORS pre-flight is needed) before redirecting:

```json
{
  "slug": "amazon",
  "target": "https://www.amazon.co.uk/",
  "country": "GB",
  "query": { "utm_source": "newsletter", "utm_campaign": "sept" },
  "referrer": "https://t.co/",
  "userAgent": "Mozilla/5.0 ...",
  "language": "en-GB",
  "timestamp": "2026-09-07T10:15:30.000Z"
}
```

`python shortener.py stats` sends a **GET** to the same URL (optionally
`?slug=<slug>`) and expects a JSON array of those records back. It then prints clicks
by slug, by country and by query string. Any backend that can store and return JSON
satisfies this contract.

### Zero-cost backend: Google Sheets

`stats/google_apps_script.gs` is a ready-made backend:

1. Create a Google Sheet. **Extensions → Apps Script**, paste the file's contents,
   save.
2. **Deploy → New deployment → Web app**. Execute as *Me*, access *Anyone*. Deploy
   and copy the web-app URL.
3. Put that URL in `.env` as `STATS_ENDPOINT=`, run `build`, commit, push.

Each click becomes a row in the sheet; `stats` reads them back through the same URL.

Other good fits: a Cloudflare Worker with KV/D1 (also gives you server-side geo), a
Supabase/PocketBase table, or any tiny Flask/FastAPI endpoint.

### Privacy note

The beacon contains the visitor's user agent and country (never the IP address —
the page only ever sees the geo API's answer). If you publish links to the general
public, mention click tracking in your privacy policy.

## Configuration reference (`.env`)

| Variable         | Default                    | Purpose                                             |
|------------------|----------------------------|-----------------------------------------------------|
| `SITE_URL`       | *(empty)*                  | Public base URL; drives `CNAME` and `list` output.  |
| `OUTPUT_DIR`     | `docs`                     | Where the site is generated.                        |
| `STATS_ENDPOINT` | *(empty = off)*            | POST target for click beacons / GET source for `stats`. |
| `GEO_API_URL`    | `https://ipapi.co/json/`   | Client-side IP → country lookup.                    |
| `GEO_TIMEOUT_MS` | `1500`                     | Max wait for geo lookup before using default URL.   |
| `SITE_TITLE`     | `Redirecting...`           | Default `<title>` for redirect pages.               |

Real environment variables override `.env`, which makes CI configuration easy.

## Limitations

- Redirects are client-side (JavaScript / meta-refresh), not HTTP 301/302. Browsers
  handle this transparently, but some link-preview bots and SEO crawlers only follow
  the `<noscript>` default URL and won't be geo-routed or counted.
- The redirect page itself is served by GitHub's CDN, which is fast but has no SLA.
- Because everything is static, changes require a build + push (≈ 1 minute to go
  live). There is no web UI for creating links — the CLI is the UI.
