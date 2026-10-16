<h1 align="center">Auto Paper Harvester</h1>

<p align="center">
Download full-text PDFs for a list of DOIs. Open access first, then the publisher's
text-and-data-mining (TDM) API if you have a key, then your own logged-in browser if you
switch it on. Polite by default: daily caps, per-site pacing, and automatic backoff.
</p>

<p align="center">
<b>English</b> · <a href="README.zh-CN.md">简体中文</a>
</p>

---

**You give it** DOIs, doi.org links, or a pasted reference list.
**You get** `papers/<doi>/<doi>.pdf` (plus supplementary PDFs when the landing page links them),
and a list of the papers it could not get, each with a reason.

It is built for the step after literature screening: you already picked the 20–200 papers
you need and want their full text, for reading, for an LLM pipeline, or for a corpus.
It only downloads what you, your API keys, or your institution can already access.

## Three ways to use it

| | For | Start with |
|---|---|---|
| 🖥 **Local web page** | setting keys and downloading without the terminal | `auto-paper-download ui` |
| ⌨️ **CLI** | scripts and pipelines | `auto-paper-download get 10.xxxx/...` |
| 🤖 **Agent skill** | Claude Code, Codex, Cursor, Hermes, OpenClaw, ... | [copy the prompt below](#use-it-from-an-ai-agent) |

## Install

```bash
uv tool install git+https://github.com/jxtse/auto-paper-harvester
# or: pipx install git+https://github.com/jxtse/auto-paper-harvester
# or: download the .whl from the Releases page and `pip install` it
```

Python 3.9+. Two dependencies (`requests`, `beautifulsoup4`).

## Quick start

```bash
auto-paper-download ui
```

This opens a page on your own machine (`127.0.0.1`, nothing is exposed to the network):

1. Enter an e-mail address. OpenAlex, Unpaywall and Crossref ask for one as a contact.
   That is enough to start.
2. Optional: add publisher API keys and connect Chrome (both explained on the page).
3. Paste DOIs or a reference list, press **Start**.

Or from the terminal:

```bash
export OPENALEX_MAILTO=you@university.edu UNPAYWALL_EMAIL=you@university.edu CROSSREF_MAILTO=you@university.edu
auto-paper-download get 10.5194/npg-20-705-2013 10.1016/j.jocs.2020.101171 -o papers
auto-paper-download get --file references.txt -o papers      # any text containing DOIs
auto-paper-download doctor                                   # what is configured
```

Keys saved in the web page live in `~/.auto-paper-download/.env` (file mode 600) and are
used by the CLI too. A `.env` in the current directory takes precedence.

## How each paper is fetched

```
DOI
 │
 ├─① Open access ........ OpenAlex → Unpaywall → Crossref full-text links      e-mail only
 │      no PDF?
 ├─② Publisher TDM API .. Elsevier · Wiley · Springer Nature                  only if you have that key
 │      no PDF?
 └─③ Your browser ....... your Chrome, your library login (Playwright Extension) only if you turn it on
```

- The first step that returns a real PDF wins (the file must start with `%PDF`;
  login pages and bot checks are rejected, not saved).
- A step you have not configured is skipped. With only an e-mail you get every paper that
  has a legal open-access copy.
- Step ③ runs in the Chrome you already use, so requests carry your own institutional
  cookies. It opens one tab, waits up to ~40 s for a bot check to clear on its own, and
  otherwise gives up on that site for 24 h. It never solves a CAPTCHA.

## Staying polite (and not getting your IP blocked)

On by default, shared across runs and processes through a small SQLite file
(`~/.auto-paper-download/ledger.sqlite`):

| Limit | Default |
|---|---|
| Successful papers per day | 40 open access · 30 per TDM publisher · 12 via browser · 80 total |
| Requests per publisher site per day | 100 (browser: 25 attempts) |
| Gap between requests to the same site | 3–12 s (random); 1–2 s for the metadata APIs; 60–120 s between browser papers |
| After HTTP 429 | site paused 6 h |
| After a CAPTCHA / Cloudflare / Akamai page | site paused 24 h |

A plain 403 (no subscription) is not treated as a block. All numbers can be changed:
[docs/RATE_LIMITS.md](docs/RATE_LIMITS.md).

## Publisher API keys (optional)

| Key | Where to get it | Covers |
|---|---|---|
| `ELSEVIER_API_KEY` | [dev.elsevier.com](https://dev.elsevier.com/apikey/manage) | Elsevier / ScienceDirect, within your institution's entitlements |
| `WILEY_TDM_TOKEN` | [Wiley TDM](https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining) | Wiley, within your institution's entitlements |
| `SPRINGER_API_KEY` | [dev.springernature.com](https://dev.springernature.com/) | Springer Nature open-access content |

Elsevier and Wiley check entitlements by IP, so run from your campus network or VPN.

## Browser step (optional)

For publishers without a TDM API (ACS, RSC, IEEE, AIP, IOP, APS, ...):

1. Install the [Playwright Extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm) in Chrome.
2. Log in to your library or the publisher in that Chrome.
3. `npm i -g @playwright/cli`
4. Click the extension icon, copy the token, paste it into `auto-paper-download ui`
   (or set `PLAYWRIGHT_MCP_EXTENSION_TOKEN`), and press **Test connection**.

Then pick "My Chrome" in the page, or add `--browser extension` on the CLI.
Without Chrome you can use a separate Chromium profile instead (`--browser playwright`,
needs `pip install 'auto-paper-download[browser]' && playwright install chromium`).
Details: [docs/BROWSER_FALLBACK.md](docs/BROWSER_FALLBACK.md).

## Use it from an AI agent

Paste this into your agent (Claude Code, Codex, Cursor, Hermes, OpenClaw, ...):

```text
Install the paper-download skill from https://github.com/jxtse/auto-paper-harvester: run `uv tool install git+https://github.com/jxtse/auto-paper-harvester` (or pipx), then `auto-paper-download skill --install <your skills folder>` (e.g. ~/.claude/skills or ~/.codex/skills), run `auto-paper-download doctor`, and tell me what is configured. Ask me for my contact e-mail if open access is not set up; do not ask me to paste API keys into the chat.
```

Then, whenever you need papers:

```text
Download the full text of these papers into ./papers with auto-paper-download (open access first, then my configured APIs; use my Chrome only if doctor says it is ready), and tell me which ones failed and why: <DOIs or reference list>
```

The skill ([`SKILL.md`](auto_paper_download/skill/SKILL.md)) tells the agent to use
`get --json`, keep the daily limits on, never solve CAPTCHAs, and report failures in
plain words. `auto-paper-download skill` prints it for agents that take a prompt file.

## CLI reference

```text
auto-paper-download get DOI... [--file F] [-o DIR] [--browser extension|playwright] [--json]
                                [--ledger PATH | --no-ledger] [--overwrite] [--dry-run] [-v]
auto-paper-download ui [--port 8765] [--no-open] [-o DIR]
auto-paper-download doctor
auto-paper-download skill [--install SKILLS_DIR]
auto-paper-download --savedrecs savedrecs.xls [...]     # Web of Science export (original interface)
```

All settings are environment variables; see [.env.example](.env.example).
Supported publishers and DOI prefixes: [docs/SUPPORTED_PUBLISHERS.md](docs/SUPPORTED_PUBLISHERS.md).

## FAQ

**Does it bypass paywalls?** No. Step ① uses legal open-access copies, step ② uses APIs
with your own key, step ③ uses your own browser session. If you cannot open a paper in
your browser, this tool cannot download it.

**Why did a paper fail?** Run with `--json` or look at the end of the output. The usual
reasons are: no open-access copy and no key for that publisher; a daily limit; a site that
is cooling down after a bot check; or no subscription.

**Is my API key sent anywhere?** Only to that publisher's API. The local page never sends
saved keys back to the browser, and it accepts requests only with the one-time token in the
URL it prints.

## Development

```bash
git clone https://github.com/jxtse/auto-paper-harvester && cd auto-paper-harvester
uv venv && uv pip install -e '.[dev]' && .venv/bin/python -m pytest -q
```

The scripts under `.claude/skills/paper-download/scripts/` are kept for existing users.

## License

Not chosen yet. Until a license is added, please open an issue before reusing the code.
