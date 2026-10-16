<h1 align="center">Auto Paper Harvester</h1>

<p align="center">
按 DOI 批量下载论文全文 PDF。先找开放获取版本，再用你自己的出版社 TDM API（有 key 才用），
最后可选用你已登录图书馆的浏览器。默认限速：每日上限、同站间隔、被拦截自动暂停。
</p>

<p align="center">
<a href="README.md">English</a> · <b>简体中文</b>
</p>

---

**输入**：DOI、doi.org 链接，或直接粘贴一段参考文献列表。
**输出**：`papers/<doi>/<doi>.pdf`（落地页上能找到补充材料 PDF 时一并下载），以及没下到的论文清单和原因。

它面向文献筛选之后的那一步：你已经挑好了真正需要的 20 到 200 篇，要拿全文来读、喂给 LLM
流程或建语料库。它只下载你本来就有权访问的内容：开放获取版本、你的 API key 覆盖的内容、你所在机构订阅的内容。

## 三种用法

| | 适合 | 从这里开始 |
|---|---|---|
| 🖥 **本地网页** | 不想碰命令行，填 key、粘 DOI、点下载 | `auto-paper-download ui` |
| ⌨️ **命令行** | 脚本和数据流程 | `auto-paper-download get 10.xxxx/...` |
| 🤖 **Agent 技能** | Claude Code、Codex、Cursor、Hermes、OpenClaw 等 | [复制下面的提示词](#让-ai-agent-帮你用) |

## 安装

```bash
uv tool install git+https://github.com/jxtse/auto-paper-harvester
# 或：pipx install git+https://github.com/jxtse/auto-paper-harvester
# 或：在 Releases 页面下载 .whl 后 pip install
```

需要 Python 3.9+，只依赖 `requests` 和 `beautifulsoup4`。

## 快速开始

```bash
auto-paper-download ui
```

会在本机打开一个网页（只监听 `127.0.0.1`，不对外暴露）：

1. 填一个邮箱。OpenAlex、Unpaywall、Crossref 要求调用方留联系邮箱，填了就能开始用。
2. 可选：填出版社 API key、连接 Chrome，页面上有步骤说明。
3. 粘贴 DOI 或参考文献，点 **开始下载**。

命令行用法：

```bash
export OPENALEX_MAILTO=you@university.edu UNPAYWALL_EMAIL=you@university.edu CROSSREF_MAILTO=you@university.edu
auto-paper-download get 10.5194/npg-20-705-2013 10.1016/j.jocs.2020.101171 -o papers
auto-paper-download get --file references.txt -o papers      # 任何含 DOI 的文本都行
auto-paper-download doctor                                   # 查看哪些步骤已配置
```

网页里保存的 key 写在 `~/.auto-paper-download/.env`（权限 600），命令行也会读取。
当前目录下的 `.env` 优先级更高。

## 每篇论文怎么下载

```
DOI
 │
 ├─① 开放获取 ......... OpenAlex → Unpaywall → Crossref 全文链接          只需邮箱
 │      没拿到 PDF？
 ├─② 出版社 TDM API ... Elsevier · Wiley · Springer Nature               有对应 key 才走
 │      没拿到 PDF？
 └─③ 你的浏览器 ....... 你的 Chrome + 图书馆登录（Playwright Extension）    你手动开启才走
```

- 哪一步先拿到真正的 PDF 就停（文件必须以 `%PDF` 开头，登录页和人机验证页会被丢弃，不会存成 PDF）。
- 没配置的步骤直接跳过。只填邮箱，就能拿到所有有合法开放获取版本的论文。
- 第 ③ 步在你正在用的 Chrome 里运行，请求带的是你自己的机构 cookie。它只开一个标签页；
  遇到人机验证会等约 40 秒看是否自动通过，不通过就放弃该网站 24 小时，绝不替你点验证码。

## 限速，避免 IP 被封

默认开启。限额记录在一个小 SQLite 文件里（`~/.auto-paper-download/ledger.sqlite`），
多次运行、多个进程共享：

| 限制 | 默认值 |
|---|---|
| 每日成功篇数 | 开放获取 40 · 每个 TDM 出版社 30 · 浏览器 12 · 合计 80 |
| 每个出版社网站每日请求数 | 100（浏览器：25 次尝试） |
| 同一网站两次请求的间隔 | 随机 3–12 秒；元数据接口 1–2 秒；浏览器每篇间隔 60–120 秒 |
| 收到 HTTP 429 | 该网站暂停 6 小时 |
| 遇到验证码、Cloudflare、Akamai 拦截页 | 该网站暂停 24 小时 |

普通 403（没有订阅）不算被封。所有数值都可以改，见 [docs/RATE_LIMITS.md](docs/RATE_LIMITS.md)。

## 出版社 API key（可选）

| Key | 申请地址 | 覆盖范围 |
|---|---|---|
| `ELSEVIER_API_KEY` | [dev.elsevier.com](https://dev.elsevier.com/apikey/manage) | Elsevier / ScienceDirect，以你机构的订阅为准 |
| `WILEY_TDM_TOKEN` | [Wiley TDM](https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining) | Wiley，以你机构的订阅为准 |
| `SPRINGER_API_KEY` | [dev.springernature.com](https://dev.springernature.com/) | Springer Nature 开放获取内容 |

Elsevier 和 Wiley 按 IP 判断订阅，请在校园网或学校 VPN 下运行。

## 浏览器步骤（可选）

用于没有 TDM API 的出版社（ACS、RSC、IEEE、AIP、IOP、APS 等）：

1. 在 Chrome 安装 [Playwright Extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm)。
2. 在这个 Chrome 里登录学校图书馆或出版社。
3. `npm i -g @playwright/cli`
4. 点扩展图标，复制 token，粘贴到 `auto-paper-download ui` 页面（或设置环境变量
   `PLAYWRIGHT_MCP_EXTENSION_TOKEN`），点 **测试连接**。

之后在网页里选“我的 Chrome”，或在命令行加 `--browser extension`。
不想用自己的 Chrome，也可以用独立 Chromium（`--browser playwright`，需要
`pip install 'auto-paper-download[browser]' && playwright install chromium`）。
详见 [docs/BROWSER_FALLBACK.md](docs/BROWSER_FALLBACK.md)。

## 让 AI Agent 帮你用

把下面这段发给你的 agent（Claude Code、Codex、Cursor、Hermes、OpenClaw 等）：

```text
从 https://github.com/jxtse/auto-paper-harvester 安装 paper-download 技能：运行 `uv tool install git+https://github.com/jxtse/auto-paper-harvester`（或用 pipx），再运行 `auto-paper-download skill --install <你的技能目录>`（例如 ~/.claude/skills 或 ~/.codex/skills），然后运行 `auto-paper-download doctor` 告诉我哪些已配置。开放获取没配好就问我要联系邮箱；不要让我把 API key 发在对话里。
```

之后要下论文时：

```text
用 auto-paper-download 把这些论文的全文下载到 ./papers（先开放获取，再用我配置好的 API；doctor 显示浏览器可用时才用我的 Chrome），告诉我哪些没下到、原因是什么：<DOI 或参考文献列表>
```

技能文件 [`SKILL.md`](auto_paper_download/skill/SKILL.md) 要求 agent 使用 `get --json`、保持每日限额、
不替用户过验证码、用普通话语解释失败原因。`auto-paper-download skill` 可直接打印技能内容，
供只接受提示词文件的 agent 使用。

## 命令参考

```text
auto-paper-download get DOI... [--file F] [-o DIR] [--browser extension|playwright] [--json]
                                [--ledger PATH | --no-ledger] [--overwrite] [--dry-run] [-v]
auto-paper-download ui [--port 8765] [--no-open] [-o DIR]
auto-paper-download doctor
auto-paper-download skill [--install SKILLS_DIR]
auto-paper-download --savedrecs savedrecs.xls [...]     # Web of Science 导出文件（原有接口）
```

所有设置都是环境变量，见 [.env.example](.env.example)。
支持的出版社和 DOI 前缀见 [docs/SUPPORTED_PUBLISHERS.md](docs/SUPPORTED_PUBLISHERS.md)。

## 常见问题

**能绕过付费墙吗？** 不能。第 ① 步用合法的开放获取版本，第 ② 步用你自己的 key 调官方 API，
第 ③ 步用你自己的浏览器登录状态。你在浏览器里打不开的论文，这个工具也下不到。

**为什么有论文没下到？** 加 `--json` 或看输出末尾。常见原因：没有开放获取版本且没有该出版社的 key；
达到每日上限；网站因人机验证在暂停期；机构没有订阅。

**API key 会被发到哪里？** 只发给对应出版社的 API。本地网页不会把已保存的 key 回传给浏览器，
并且只接受带有启动时打印的一次性 token 的请求。

## 开发

```bash
git clone https://github.com/jxtse/auto-paper-harvester && cd auto-paper-harvester
uv venv && uv pip install -e '.[dev]' && .venv/bin/python -m pytest -q
```

`.claude/skills/paper-download/scripts/` 下的旧脚本保留给已有用户。

## 许可证

尚未确定。在加入许可证之前，如需复用代码请先开 issue 联系。
