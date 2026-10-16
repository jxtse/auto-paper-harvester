<h1 align="center">Auto Paper Harvester</h1>

<p align="center">
<b>English</b> · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
<a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
<img alt="Python" src="https://img.shields.io/badge/python-3.9%2B-blue.svg">
<a href="https://github.com/jxtse/auto-paper-harvester/releases/latest"><img alt="release" src="https://img.shields.io/github/v/release/jxtse/auto-paper-harvester"></a>
<a href="https://github.com/jxtse/auto-paper-harvester/actions/workflows/test.yml"><img alt="tests" src="https://github.com/jxtse/auto-paper-harvester/actions/workflows/test.yml/badge.svg"></a>
</p>

Auto Paper Harvester downloads the full-text PDFs of papers you have already chosen.
Give it DOIs, doi.org links, or a pasted reference list; it saves one folder per paper and
tells you which ones it could not get and why.

It is meant for the step after literature screening: you have picked the 20–200 papers you
need and want their full text for reading, for an LLM pipeline, or for a corpus. It only
fetches what you can already access legally: open-access copies, content your publisher API
keys cover, and content your institution subscribes to.

You can use it from an AI agent, from a local web page, or from the command line.

## How it works

For each paper it tries three steps in order and stops at the first real PDF:

```
DOI
 ├─ 1. Open access ......... OpenAlex → Unpaywall → Crossref          needs only an e-mail
 ├─ 2. Publisher TDM API ... Elsevier · Wiley · Springer Nature       only if you have that key
 └─ 3. Your own browser .... your Chrome with your library login      only if you turn it on
```

- Steps you have not set up are skipped. With just an e-mail address you get every paper
  that has a legal open-access copy.
- A file counts only if it is a real PDF; login pages and bot checks are thrown away.
- Step 3 runs in the Chrome you already use, one tab at a time. If a site shows a CAPTCHA,
  it leaves that site alone for 24 hours. It never solves CAPTCHAs.
- Downloads are rate-limited by default (daily caps, a few seconds between requests to the
  same site, pauses after HTTP 429 or a bot check) so your IP does not get blocked.
  See [docs/RATE_LIMITS.md](docs/RATE_LIMITS.md).

Output: `papers/<doi>/<doi>.pdf`, plus supplementary PDFs when the landing page links them.

## Quick start

### With an AI agent

Paste this into Claude Code, Codex, Cursor, Hermes, OpenClaw, or any agent that can run
shell commands:

```text
Install the paper-download skill from https://github.com/jxtse/auto-paper-harvester: run `uv tool install git+https://github.com/jxtse/auto-paper-harvester` (or pipx), then `auto-paper-download skill --install <your skills folder>` (e.g. ~/.claude/skills or ~/.codex/skills), run `auto-paper-download doctor`, and tell me what is configured. Ask me for my contact e-mail if open access is not set up; do not ask me to paste API keys into the chat.
```

After that, ask for papers like this:

```text
Download the full text of these papers into ./papers with auto-paper-download, and tell me which ones failed and why: <DOIs or reference list>
```

### Without Python: download the app

Get the file for your system from the
[latest release](https://github.com/jxtse/auto-paper-harvester/releases/latest), unzip it, and
double-click `auto-paper-harvester`. The page opens in your browser.

| System | File |
|---|---|
| macOS (Apple silicon) | `auto-paper-harvester-macos-arm64.zip` |
| Windows | `auto-paper-harvester-windows-x64.zip` |
| Linux | `auto-paper-harvester-linux-x64.tar.gz` |

The files are not code-signed. macOS: allow it once under **System Settings → Privacy &
Security → Open Anyway**. Windows: **More info → Run anyway**.

### With Python

```bash
uv tool install git+https://github.com/jxtse/auto-paper-harvester   # or: pipx install ...
```

Open the web page:

```bash
auto-paper-download ui
```

Or use the command line:

```bash
export OPENALEX_MAILTO=you@university.edu UNPAYWALL_EMAIL=you@university.edu CROSSREF_MAILTO=you@university.edu
auto-paper-download get 10.5194/npg-20-705-2013 -o papers
auto-paper-download get --file references.txt -o papers   # any text that contains DOIs
auto-paper-download doctor                                # what is set up
```

## Optional setup

Both can be done on the web page, which saves them to `~/.auto-paper-download/.env`.

**Publisher API keys** (step 2). Elsevier and Wiley check your institution's subscription by
IP, so run from campus or VPN.

| Key | Get it at |
|---|---|
| `ELSEVIER_API_KEY` | [dev.elsevier.com](https://dev.elsevier.com/apikey/manage) |
| `WILEY_TDM_TOKEN` | [Wiley TDM](https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining) |
| `SPRINGER_API_KEY` | [dev.springernature.com](https://dev.springernature.com/) |

**Your own Chrome** (step 3), for publishers without an API such as ACS, RSC, IEEE or IOP:
install the [Playwright Extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm),
log in to your library in Chrome, run `npm i -g @playwright/cli`, and paste the extension's
token into the web page. Details: [docs/BROWSER_FALLBACK.md](docs/BROWSER_FALLBACK.md).

## Reference

```text
auto-paper-download get DOI... [--file F] [-o DIR] [--browser extension|playwright] [--json] [--no-ledger]
auto-paper-download ui [--port 8765] [--no-open]
auto-paper-download doctor
auto-paper-download skill [--install SKILLS_DIR]
```

- All settings: [.env.example](.env.example)
- Supported publishers: [docs/SUPPORTED_PUBLISHERS.md](docs/SUPPORTED_PUBLISHERS.md)
- Rate limits: [docs/RATE_LIMITS.md](docs/RATE_LIMITS.md)
- Development: `uv venv && uv pip install -e '.[dev]' && .venv/bin/python -m pytest -q`

## License

[MIT](LICENSE). Downloaded papers keep their own licenses and the publishers' terms.
