# Browser Fallback Configuration Guide

The `--use-browser-fallback` flag enables a Playwright-driven second pass that
retries every DOI the HTTP/OA pipeline failed, using a real browser session with
the user's institutional cookies. This guide covers everything beyond the basics
in [the main README](../README.md) and [the skill runbook](../.claude/skills/paper-download/SKILL.md).

## Installation

```bash
pip install -e '.[browser]'        # from the cloned repo root
playwright install chromium        # ~150 MB Chromium, one-time
```

First run opens a real Chromium window so you can log into your institution's
SSO once. Cookies persist for subsequent runs.

## Browser choice

By default the fallback uses **Playwright's bundled Chromium** — most portable,
works identically on macOS / Linux / Windows, no system browser needed.

To reuse a system-wide browser (e.g. you've already logged into SSO in Chrome /
Edge on your daily-driver machine and don't want to redo it in a clean profile),
set `BROWSER_FALLBACK_CHANNEL`:

| Channel value | Browser | Notes |
|---|---|---|
| (unset, default) | Bundled Chromium | Most portable; installed via `playwright install chromium` |
| `chrome` | Google Chrome (stable) | Must be installed system-wide |
| `chrome-beta` / `chrome-dev` / `chrome-canary` | Chrome pre-release channels | |
| `msedge` | Microsoft Edge (stable) | Recommended on Windows |
| `msedge-beta` / `msedge-dev` / `msedge-canary` | Edge pre-release channels | |

Brave / Vivaldi / Arc / Opera aren't first-class Playwright channels but
typically work by pointing `BROWSER_FALLBACK_PROFILE` at their Chromium-based
profile dir (they share the same persistent context format).

Example (macOS, reusing Chrome):

```bash
export BROWSER_FALLBACK_CHANNEL=chrome
python -m auto_paper_download --savedrecs savedrecs.xls --use-browser-fallback
```

### Decision matrix

| If you... | Use |
|---|---|
| Just want it to work, no extra setup | Default (`playwright install chromium`) |
| Already have Chrome/Edge installed and don't want a second 150 MB download | `BROWSER_FALLBACK_CHANNEL=chrome` (or `msedge`) |
| On Windows with institutional SSO bookmarked in Edge | `BROWSER_FALLBACK_CHANNEL=msedge` |
| Running in CI / Docker / headless server | Default Chromium + `BROWSER_FALLBACK_HEADLESS=1` |

## ⚠️ Persistent profile safety

The fallback uses Playwright's `launch_persistent_context`, which writes to a
real Chromium profile directory. By default we create a **dedicated isolated
profile** at the cache path below — so your real browsing session stays untouched.

If you point `BROWSER_FALLBACK_PROFILE` at your real Chrome/Edge user-data dir
(e.g. `~/Library/Application Support/Google/Chrome`), be aware:

- The browser **must be fully closed** while the fallback runs (Playwright
  needs exclusive access to the profile lock).
- Any extensions/sessions in that profile are loaded, including ad blockers
  that may hide the PDF download button.
- Running in headless mode against a real profile is brittle (many sites
  detect it).

**Recommendation**: keep the default isolated profile and log into SSO once in
the fallback's own window.

## Profile location

The persistent profile lives under a platform-specific cache directory by default:

| Platform | Default profile path |
|---|---|
| Linux | `${XDG_CACHE_HOME:-~/.cache}/auto_paper_download/browser_profile/` |
| macOS | `~/.cache/auto_paper_download/browser_profile/` (⚠️ see known issue) |
| Windows | `~/.cache/auto_paper_download/browser_profile/` (⚠️ see known issue) |

> ⚠️ **Known issue (v0.2.0)**: the implementation hard-codes the Linux-style
> `~/.cache/` path on all platforms. Strictly speaking macOS profiles should
> live under `~/Library/Caches/auto_paper_download/` and Windows under
> `%LOCALAPPDATA%\auto_paper_download\`. The path still works on all three
> OSes — just not in the platform-conventional location. Set
> `BROWSER_FALLBACK_PROFILE` explicitly if this matters to you.

## Headless vs headed

`BROWSER_FALLBACK_HEADLESS` overrides the default. When unset, the resolver picks:

| Situation | Default | Why |
|---|---|---|
| `family == acs` or `wiley` | **headed** | Their SI capture is unreliable in headless mode |
| macOS / Windows (desktop) | **headed** | Always have a display; user can complete SSO interactively |
| Linux with `DISPLAY` or `WAYLAND_DISPLAY` | **headed** | X11/Wayland session detected |
| Linux without display (CI, headless servers) | **headless** | No GUI available |
| macOS via SSH without screen sharing | headed by default — **set `BROWSER_FALLBACK_HEADLESS=1`** | Heuristic can't detect this; explicit override is required |

## All environment variables

| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_FALLBACK_ENABLED` | `1` | Set to `0` to disable globally (useful for CI) |
| `BROWSER_FALLBACK_HEADLESS` | auto | `1` forces headless, `0` forces headed |
| `BROWSER_FALLBACK_CHANNEL` | (bundled chromium) | `chrome` / `msedge` / `chrome-beta` / ... |
| `BROWSER_FALLBACK_PROFILE` | platform cache path | Override profile dir |
| `BROWSER_FALLBACK_AUTH_HOSTS` | (none) | Comma-list of extra SSO host substrings to detect auth-redirect |

## Behavior details

- Runs as a **second pass**: every DOI the API pipeline failed gets one retry.
- Uses publisher-specific CSS selectors (see `auto_paper_download/browser_fallback.py`
  → `PUBLISHER_PDF_SELECTORS`) plus a generic heuristic.
- Bounces to SSO → marked `auth_redirect`, surfaces as a residual failure with
  the reason; user logs in once and reruns.
- Headed mode is the default for ACS / Wiley (their SI capture is unreliable
  in headless).
- PDF integrity check (`%PDF-` magic bytes) — non-PDF payloads (CAPTCHA HTML)
  are rejected with `challenge_timeout`.

## Troubleshooting

- **`playwright: command not found`** → `pip install playwright && playwright install chromium`
- **`no_link` returned for a publisher you have access to** → that publisher updated
  their layout; add a CSS selector to `PUBLISHER_PDF_SELECTORS` in
  `auto_paper_download/browser_fallback.py`. See
  [SUPPORTED_PUBLISHERS.md](SUPPORTED_PUBLISHERS.md#adding-a-new-publisher).
- **`auth_redirect` on every run** → your institution uses an SSO host the
  detector doesn't recognise. Add the host substring to
  `BROWSER_FALLBACK_AUTH_HOSTS=...` (comma-separated).
- **`challenge_timeout`** → the publisher served a CAPTCHA or login HTML when
  it should have returned a PDF. Usually means your institutional session expired;
  log in again via the Chromium window and rerun.
- **First-run profile is empty / no SSO** → make sure you ran a session at least
  once in headed mode (don't set `BROWSER_FALLBACK_HEADLESS=1` for the first run).
