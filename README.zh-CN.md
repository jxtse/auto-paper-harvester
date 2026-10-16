<h1 align="center">Auto Paper Harvester</h1>

<p align="center">
<a href="README.md">English</a> · <b>简体中文</b>
</p>

<p align="center">
<a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
<img alt="Python" src="https://img.shields.io/badge/python-3.9%2B-blue.svg">
<a href="https://github.com/jxtse/auto-paper-harvester/releases/latest"><img alt="release" src="https://img.shields.io/github/v/release/jxtse/auto-paper-harvester"></a>
<a href="https://github.com/jxtse/auto-paper-harvester/actions/workflows/test.yml"><img alt="tests" src="https://github.com/jxtse/auto-paper-harvester/actions/workflows/test.yml/badge.svg"></a>
</p>

Auto Paper Harvester 用来批量下载你已经选好的论文的全文 PDF。
给它 DOI、doi.org 链接，或者直接粘贴一段参考文献，它会把每篇论文存到单独的文件夹，
并告诉你哪些没下到、原因是什么。

它面向文献筛选之后的那一步：你已经挑出真正需要的 20 到 200 篇，要拿全文来读、喂给 LLM
流程或建语料库。它只下载你本来就能合法访问的内容：开放获取版本、你的出版社 API key
覆盖的内容、你所在机构订阅的内容。

可以让 AI agent 调用，也可以用本地网页或命令行。

## 工作流程

每篇论文按顺序尝试三步，拿到真正的 PDF 就停：

```
DOI
 ├─ 1. 开放获取 ........... OpenAlex → Unpaywall → Crossref        只需要一个邮箱
 ├─ 2. 出版社 TDM API ..... Elsevier · Wiley · Springer Nature     有对应 key 才走
 └─ 3. 你自己的浏览器 ..... 你的 Chrome 和图书馆登录状态           你手动开启才走
```

- 没配置的步骤直接跳过。只填一个邮箱，就能拿到所有有合法开放获取版本的论文。
- 只有真正的 PDF 才算下载成功，登录页和人机验证页会被丢弃。
- 第 3 步在你正在用的 Chrome 里进行，一次只开一个标签页。某个网站弹出验证码时，
  24 小时内不再访问它，也不会替你点验证码。
- 默认限速：每日上限、同一网站请求之间间隔几秒、遇到 HTTP 429 或人机验证自动暂停，
  避免 IP 被封。详见 [docs/RATE_LIMITS.md](docs/RATE_LIMITS.md)。

输出：`papers/<doi>/<doi>.pdf`，落地页上有补充材料 PDF 时一并下载。

## 快速开始

### 用 AI agent

把下面这段发给 Claude Code、Codex、Cursor、Hermes、OpenClaw，或任何能运行命令的 agent：

```text
从 https://github.com/jxtse/auto-paper-harvester 安装 paper-download 技能：运行 `uv tool install git+https://github.com/jxtse/auto-paper-harvester`（或用 pipx），再运行 `auto-paper-download skill --install <你的技能目录>`（例如 ~/.claude/skills 或 ~/.codex/skills），然后运行 `auto-paper-download doctor` 告诉我哪些已配置。开放获取没配好就问我要联系邮箱；不要让我把 API key 发在对话里。
```

之后这样让它下载：

```text
用 auto-paper-download 把这些论文的全文下载到 ./papers，告诉我哪些没下到、原因是什么：<DOI 或参考文献列表>
```

### 没有 Python：下载应用

在 [最新 release](https://github.com/jxtse/auto-paper-harvester/releases/latest) 下载对应系统的文件，
解压后双击 `auto-paper-harvester`，浏览器会自动打开页面。

| 系统 | 文件 |
|---|---|
| macOS（Apple 芯片） | `auto-paper-harvester-macos-arm64.zip` |
| Windows | `auto-paper-harvester-windows-x64.zip` |
| Linux | `auto-paper-harvester-linux-x64.tar.gz` |

这些文件没有代码签名。macOS 第一次打开需要到 **系统设置 → 隐私与安全性** 点 **仍要打开**；
Windows 点 **更多信息 → 仍要运行**。

### 有 Python

```bash
uv tool install git+https://github.com/jxtse/auto-paper-harvester   # 或：pipx install ...
```

打开网页：

```bash
auto-paper-download ui
```

或用命令行：

```bash
export OPENALEX_MAILTO=you@university.edu UNPAYWALL_EMAIL=you@university.edu CROSSREF_MAILTO=you@university.edu
auto-paper-download get 10.5194/npg-20-705-2013 -o papers
auto-paper-download get --file references.txt -o papers   # 任何包含 DOI 的文本
auto-paper-download doctor                                # 查看哪些已配置
```

## 可选配置

以下两项都可以在网页里完成，设置保存在 `~/.auto-paper-download/.env`。

**出版社 API key**（第 2 步）。Elsevier 和 Wiley 按 IP 判断机构订阅，请在校园网或学校 VPN 下运行。

| Key | 申请地址 |
|---|---|
| `ELSEVIER_API_KEY` | [dev.elsevier.com](https://dev.elsevier.com/apikey/manage) |
| `WILEY_TDM_TOKEN` | [Wiley TDM](https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining) |
| `SPRINGER_API_KEY` | [dev.springernature.com](https://dev.springernature.com/) |

**你自己的 Chrome**（第 3 步），用于 ACS、RSC、IEEE、IOP 等没有 API 的出版社：
安装 [Playwright Extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm)，
在 Chrome 里登录学校图书馆，运行 `npm i -g @playwright/cli`，再把扩展里的 token 粘贴到网页。
详见 [docs/BROWSER_FALLBACK.md](docs/BROWSER_FALLBACK.md)。

## 参考

```text
auto-paper-download get DOI... [--file F] [-o DIR] [--browser extension|playwright] [--json] [--no-ledger]
auto-paper-download ui [--port 8765] [--no-open]
auto-paper-download doctor
auto-paper-download skill [--install SKILLS_DIR]
```

- 全部设置项：[.env.example](.env.example)
- 支持的出版社：[docs/SUPPORTED_PUBLISHERS.md](docs/SUPPORTED_PUBLISHERS.md)
- 限速规则：[docs/RATE_LIMITS.md](docs/RATE_LIMITS.md)
- 开发：`uv venv && uv pip install -e '.[dev]' && .venv/bin/python -m pytest -q`

## 许可证

[MIT](LICENSE)。下载到的论文仍受其原有许可和出版社条款约束。
