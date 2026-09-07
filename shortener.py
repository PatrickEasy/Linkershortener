#!/usr/bin/env python3
"""
shortener.py - a static link shortener that deploys to GitHub Pages.

GitHub Pages can only serve static files, so this tool works as a *generator*:
you describe your short links in ``links.json`` (via the CLI), and ``build``
turns each one into a tiny HTML page under ``docs/<slug>/index.html``.  When
someone visits ``https://your.domain/<slug>`` GitHub Pages serves that page,
whose JavaScript then:

  1. reads the query string (``?utm_source=...``) and, if enabled, forwards it
     on to the destination;
  2. looks up the visitor's country with a free client-side IP geolocation API
     and picks a per-country destination if one is configured
     (e.g. GB -> amazon.co.uk, everyone else -> amazon.com.au);
  3. fires a click-tracking beacon to ``STATS_ENDPOINT`` (if configured) with
     the slug, country, query string, referrer and user agent;
  4. redirects the browser with ``location.replace``.

A ``<noscript>`` meta-refresh fallback sends JS-less visitors (and most link
crawlers) to the default destination.

Usage (see README.md for the full walk-through)::

    python shortener.py add amazon https://www.amazon.com.au/ \
        --geo GB=https://www.amazon.co.uk/ --geo US=https://www.amazon.com/
    python shortener.py add promo https://example.com/sale --forward-query
    python shortener.py list
    python shortener.py build
    python shortener.py serve            # local preview on http://localhost:8000
    python shortener.py remove promo
    python shortener.py stats            # pull click stats from STATS_ENDPOINT

Configuration lives in ``.env`` (copy ``.env.example``).  Nothing in ``.env``
is secret except, potentially, ``STATS_ENDPOINT`` - keep it out of git anyway.

Requires Python 3.9+.  Standard library only; Logpy (Patrick's utilities
package) is used for nicer console output when it is installed, with a
plain-print fallback so the script also runs in CI / for anyone cloning it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import string
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Optional Logpy integration (falls back to plain print if not installed)
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - exercised only when Logpy is installed
    from Logpy import err, info, ok, printtime, title  # type: ignore

    HAVE_LOGPY = True
except Exception:  # noqa: BLE001 - any import failure means "not available"
    HAVE_LOGPY = False

    def printtime(message: str, **_: Any) -> None:  # type: ignore[misc]
        print(f"[{datetime.now():%H:%M:%S}] {message}")

    def ok(message: str) -> None:  # type: ignore[misc]
        print(f"[OK]   {message}")

    def info(message: str) -> None:  # type: ignore[misc]
        print(f"[INFO] {message}")

    def err(message: str) -> None:  # type: ignore[misc]
        print(f"[ERR]  {message}", file=sys.stderr)

    def title(message: str) -> None:  # type: ignore[misc]
        print(f"\n== {message} ==")


# --------------------------------------------------------------------------- #
# Paths & configuration
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
LINKS_FILE = ROOT / "links.json"
ENV_FILE = ROOT / ".env"

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")  # ISO 3166-1 alpha-2, e.g. GB, AU, US

DEFAULTS: dict[str, str] = {
    "SITE_URL": "",  # e.g. https://go.example.com  (no trailing slash)
    "OUTPUT_DIR": "docs",  # GitHub Pages "/docs" folder on the main branch
    "STATS_ENDPOINT": "",  # POST target for click beacons; empty = disabled
    "GEO_API_URL": "https://ipapi.co/json/",  # must return JSON with country_code
    "GEO_TIMEOUT_MS": "1500",  # give up on geo lookup after this and use default
    "SITE_TITLE": "Redirecting...",
}


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Read KEY=VALUE lines from .env (no external dependency needed).

    Real environment variables take precedence over the file, so CI can
    override anything without touching .env.  Lines starting with '#' and
    blank lines are ignored; surrounding quotes on values are stripped.
    """
    cfg = dict(DEFAULTS)
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            cfg[key] = value
    for key in DEFAULTS:
        if key in os.environ:
            cfg[key] = os.environ[key]
    cfg["SITE_URL"] = cfg["SITE_URL"].rstrip("/")
    return cfg


# --------------------------------------------------------------------------- #
# links.json helpers
# --------------------------------------------------------------------------- #
def load_links() -> dict[str, dict[str, Any]]:
    if not LINKS_FILE.exists():
        return {}
    with LINKS_FILE.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"{LINKS_FILE.name} must contain a JSON object")
    return data


def save_links(links: dict[str, dict[str, Any]]) -> None:
    ordered = {k: links[k] for k in sorted(links)}
    LINKS_FILE.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def random_slug(length: int = 6) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def validate_url(url: str) -> str:
    if not re.match(r"^https?://", url, re.I):
        raise SystemExit(f"URL must start with http:// or https://  (got: {url})")
    return url


def parse_geo(pairs: list[str] | None) -> dict[str, str]:
    """Turn ['GB=https://a', 'US=https://b'] into {'GB': 'https://a', ...}."""
    geo: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--geo expects CC=URL, got: {pair}")
        cc, _, url = pair.partition("=")
        cc = cc.strip().upper()
        if not COUNTRY_RE.match(cc):
            raise SystemExit(f"Country code must be 2 letters (ISO 3166-1), got: {cc}")
        geo[cc] = validate_url(url.strip())
    return geo


# --------------------------------------------------------------------------- #
# HTML generation
# --------------------------------------------------------------------------- #
REDIRECT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<noscript><meta http-equiv="refresh" content="0; url={default_url_attr}"></noscript>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         display: flex; align-items: center; justify-content: center; height: 100vh;
         margin: 0; color: #444; background: #fafafa; }}
  a {{ color: #0366d6; }}
</style>
</head>
<body>
<p>Redirecting&hellip; <a id="fallback" href="{default_url_attr}">Click here if nothing happens.</a></p>
<script>
(function () {{
  var CONFIG = {config_json};

  var params = new URLSearchParams(window.location.search);
  var query = {{}};
  params.forEach(function (v, k) {{ query[k] = v; }});

  function withQuery(url) {{
    if (!CONFIG.forwardQuery || !window.location.search) return url;
    var joiner = url.indexOf("?") === -1 ? "?" : "&";
    return url + joiner + window.location.search.slice(1);
  }}

  function track(country, target) {{
    if (!CONFIG.statsEndpoint) return;
    try {{
      var payload = JSON.stringify({{
        slug: CONFIG.slug,
        target: target,
        country: country || null,
        query: query,
        referrer: document.referrer || null,
        userAgent: navigator.userAgent,
        language: navigator.language || null,
        timestamp: new Date().toISOString()
      }});
      if (navigator.sendBeacon) {{
        navigator.sendBeacon(CONFIG.statsEndpoint, new Blob([payload], {{ type: "text/plain" }}));
      }} else {{
        fetch(CONFIG.statsEndpoint, {{ method: "POST", body: payload, keepalive: true, mode: "no-cors" }});
      }}
    }} catch (e) {{ /* never block the redirect on analytics */ }}
  }}

  function go(country) {{
    var target = (country && CONFIG.geo[country]) || CONFIG.defaultUrl;
    target = withQuery(target);
    document.getElementById("fallback").href = target;
    track(country, target);
    window.location.replace(target);
  }}

  // Geo lookup only when this link has per-country rules; otherwise go straight away.
  if (!Object.keys(CONFIG.geo).length) {{ go(null); return; }}

  var done = false;
  var timer = setTimeout(function () {{ if (!done) {{ done = true; go(null); }} }}, CONFIG.geoTimeoutMs);
  fetch(CONFIG.geoApiUrl, {{ cache: "no-store" }})
    .then(function (r) {{ return r.json(); }})
    .then(function (data) {{
      if (done) return; done = true; clearTimeout(timer);
      var cc = (data.country_code || data.country || data.countryCode || "").toString().toUpperCase();
      go(cc || null);
    }})
    .catch(function () {{ if (done) return; done = true; clearTimeout(timer); go(null); }});
}})();
</script>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{site_name}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 640px; margin: 4rem auto; padding: 0 1rem; color: #333; }}
  code {{ background: #f2f2f2; padding: .1em .3em; border-radius: 3px; }}
</style>
</head>
<body>
<h1>{site_name}</h1>
<p>This is a link shortener. Short links look like <code>{site_url}/&lt;slug&gt;</code>.</p>
<p><small>Generated {generated}</small></p>
</body>
</html>
"""

NOT_FOUND_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Link not found</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 640px; margin: 4rem auto; padding: 0 1rem; color: #333; }}
</style>
</head>
<body>
<h1>404 &ndash; that short link doesn't exist</h1>
<p>Check the link for typos, or <a href="{site_url}/">go to the home page</a>.</p>
</body>
</html>
"""


def render_redirect(slug: str, entry: dict[str, Any], cfg: dict[str, str]) -> str:
    default_url = entry["url"]
    config = {
        "slug": slug,
        "defaultUrl": default_url,
        "geo": entry.get("geo", {}),
        "forwardQuery": bool(entry.get("forward_query", False)),
        "statsEndpoint": cfg["STATS_ENDPOINT"],
        "geoApiUrl": cfg["GEO_API_URL"],
        "geoTimeoutMs": int(cfg["GEO_TIMEOUT_MS"]),
    }
    # `</` inside a <script> would end the script early; JSON-escape it.
    config_json = json.dumps(config).replace("</", "<\\/")
    return REDIRECT_TEMPLATE.format(
        title=escape(entry.get("title") or cfg["SITE_TITLE"]),
        default_url_attr=escape(default_url, quote=True),
        config_json=config_json,
    )


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_add(args: argparse.Namespace) -> None:
    links = load_links()
    slug = args.slug or random_slug()
    if not SLUG_RE.match(slug):
        raise SystemExit("Slug may contain letters, digits, '-' and '_' only (max 64 chars).")
    if slug in links and not args.force:
        raise SystemExit(f"Slug '{slug}' already exists. Use --force to overwrite or 'remove' first.")

    entry: dict[str, Any] = {
        "url": validate_url(args.url),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    geo = parse_geo(args.geo)
    if geo:
        entry["geo"] = geo
    if args.forward_query:
        entry["forward_query"] = True
    if args.title:
        entry["title"] = args.title
    if args.note:
        entry["note"] = args.note

    links[slug] = entry
    save_links(links)
    cfg = load_env()
    ok(f"Added '{slug}' -> {entry['url']}")
    for cc, url in geo.items():
        info(f"  {cc} visitors -> {url}")
    if cfg["SITE_URL"]:
        info(f"Short link: {cfg['SITE_URL']}/{slug}")
    info("Run `python shortener.py build` to regenerate the site.")


def cmd_remove(args: argparse.Namespace) -> None:
    links = load_links()
    if args.slug not in links:
        raise SystemExit(f"No such slug: {args.slug}")
    del links[args.slug]
    save_links(links)
    ok(f"Removed '{args.slug}'. Run `build` to update the site (stale page will be deleted).")


def cmd_list(_: argparse.Namespace) -> None:
    links = load_links()
    cfg = load_env()
    if not links:
        info("No links yet. Add one with: python shortener.py add <slug> <url>")
        return
    title(f"{len(links)} short link(s)")
    width = max(len(s) for s in links)
    for slug, entry in sorted(links.items()):
        prefix = f"{cfg['SITE_URL']}/" if cfg["SITE_URL"] else "/"
        flags = []
        if entry.get("forward_query"):
            flags.append("forwards query")
        print(f"  {prefix}{slug:<{width}}  ->  {entry['url']}" + (f"   [{', '.join(flags)}]" if flags else ""))
        for cc, url in sorted(entry.get("geo", {}).items()):
            print(f"  {'':<{len(prefix) + width}}      {cc}: {url}")
        if entry.get("note"):
            print(f"  {'':<{len(prefix) + width}}      note: {entry['note']}")


def cmd_build(_: argparse.Namespace) -> None:
    cfg = load_env()
    links = load_links()
    out = ROOT / cfg["OUTPUT_DIR"]
    out.mkdir(parents=True, exist_ok=True)

    printtime(f"Building {len(links)} link(s) into {out.relative_to(ROOT)}/")

    # Remove pages for slugs that no longer exist so deletions actually deploy.
    for child in out.iterdir():
        if child.is_dir() and (child / "index.html").exists() and child.name not in links:
            (child / "index.html").unlink()
            try:
                child.rmdir()
            except OSError:
                pass
            info(f"Removed stale page: {child.name}/")

    for slug, entry in links.items():
        page_dir = out / slug
        page_dir.mkdir(exist_ok=True)
        (page_dir / "index.html").write_text(render_redirect(slug, entry, cfg), encoding="utf-8")
        geo_note = f" (+{len(entry.get('geo', {}))} geo rule(s))" if entry.get("geo") else ""
        ok(f"{slug}/ -> {entry['url']}{geo_note}")

    site_url = cfg["SITE_URL"] or ""
    site_name = site_url.replace("https://", "").replace("http://", "") or "Link shortener"
    (out / "index.html").write_text(
        INDEX_TEMPLATE.format(
            site_name=escape(site_name),
            site_url=escape(site_url or ""),
            generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        ),
        encoding="utf-8",
    )
    (out / "404.html").write_text(NOT_FOUND_TEMPLATE.format(site_url=escape(site_url)), encoding="utf-8")
    # Tell GitHub Pages not to run Jekyll (faster, and keeps folders starting with '_').
    (out / ".nojekyll").write_text("", encoding="utf-8")

    cname = out / "CNAME"
    host = re.sub(r"^https?://", "", site_url).split("/")[0]
    if host and not host.endswith("github.io"):
        cname.write_text(host + "\n", encoding="utf-8")
        info(f"CNAME -> {host}")
    elif cname.exists():
        cname.unlink()

    if not cfg["STATS_ENDPOINT"]:
        info("STATS_ENDPOINT not set - pages will redirect but not report clicks.")
    ok("Build complete. Commit and push to deploy via GitHub Pages.")


def cmd_serve(args: argparse.Namespace) -> None:
    import functools
    import http.server

    cfg = load_env()
    directory = ROOT / cfg["OUTPUT_DIR"]
    if not directory.exists():
        raise SystemExit("Nothing built yet - run `python shortener.py build` first.")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    info(f"Serving {directory.relative_to(ROOT)}/ at http://localhost:{args.port}/  (Ctrl+C to stop)")
    try:
        http.server.ThreadingHTTPServer(("", args.port), handler).serve_forever()
    except KeyboardInterrupt:
        print()


def cmd_stats(args: argparse.Namespace) -> None:
    """Pull click statistics from the stats endpoint.

    The endpoint contract is deliberately tiny (see README > Statistics):
    a GET on STATS_ENDPOINT (optionally with ?slug=...) returns a JSON array of
    click records with the same fields the beacon sends.  This command
    aggregates them locally so any backend that can store and return JSON
    works - Google Apps Script, a Cloudflare Worker, a tiny Flask app, etc.
    """
    cfg = load_env()
    endpoint = cfg["STATS_ENDPOINT"]
    if not endpoint:
        raise SystemExit(
            "STATS_ENDPOINT is not set in .env - there is nowhere to read stats from yet.\n"
            "See README.md > Statistics for how to wire up a backend."
        )
    url = endpoint
    if args.slug:
        url += ("&" if "?" in endpoint else "?") + "slug=" + args.slug
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 - user-configured URL
            records = json.load(resp)
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not fetch stats from {url}: {exc}") from exc
    if not isinstance(records, list):
        raise SystemExit("Stats endpoint must return a JSON array of click records.")

    if args.json:
        print(json.dumps(records, indent=2))
        return

    title(f"{len(records)} click(s)" + (f" for '{args.slug}'" if args.slug else ""))
    by_slug: dict[str, int] = {}
    by_country: dict[str, int] = {}
    by_query: dict[str, int] = {}
    for rec in records:
        by_slug[rec.get("slug", "?")] = by_slug.get(rec.get("slug", "?"), 0) + 1
        cc = rec.get("country") or "unknown"
        by_country[cc] = by_country.get(cc, 0) + 1
        q = rec.get("query") or {}
        key = "&".join(f"{k}={v}" for k, v in sorted(q.items())) if q else "(none)"
        by_query[key] = by_query.get(key, 0) + 1

    def table(name: str, counts: dict[str, int]) -> None:
        print(f"\n{name}")
        for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>6}  {key}")

    table("Clicks by slug", by_slug)
    table("Clicks by country", by_country)
    table("Clicks by query string", by_query)


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shortener.py",
        description="Static link shortener for GitHub Pages with geo-routing and click stats.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="add (or overwrite) a short link")
    a.add_argument("slug", nargs="?", help="short path, e.g. 'amazon' (random if omitted)")
    a.add_argument("url", help="default destination URL")
    a.add_argument("--geo", action="append", metavar="CC=URL",
                   help="per-country destination, e.g. GB=https://www.amazon.co.uk/ (repeatable)")
    a.add_argument("--forward-query", action="store_true",
                   help="append the short link's query string to the destination URL")
    a.add_argument("--title", help="<title> of the redirect page (shown briefly / in previews)")
    a.add_argument("--note", help="free-text note stored with the link")
    a.add_argument("--force", action="store_true", help="overwrite an existing slug")
    a.set_defaults(func=cmd_add)

    r = sub.add_parser("remove", help="delete a short link")
    r.add_argument("slug")
    r.set_defaults(func=cmd_remove)

    sub.add_parser("list", help="show all short links").set_defaults(func=cmd_list)
    sub.add_parser("build", help="generate the static site into OUTPUT_DIR").set_defaults(func=cmd_build)

    s = sub.add_parser("serve", help="preview the built site locally")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    st = sub.add_parser("stats", help="fetch and summarise click statistics")
    st.add_argument("--slug", help="only this slug")
    st.add_argument("--json", action="store_true", help="dump raw records as JSON")
    st.set_defaults(func=cmd_stats)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
