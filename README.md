# Auto Paper Download

`auto-paper-download` 是一个命令行工具，可从 Web of Science 导出的 `savedrecs.xls` 文件中提取 DOI，根据出版商自动选择合适的接口并批量下载 PDF。目前内置支持：

- **Wiley** TDM API  
- **Elsevier** TDM API  
- **Springer Nature** Open Access API（仅开放获取内容）  
- **OpenAlex**（抓取开放获取版本）  
- **Crossref**（作为 OpenAlex 的备用方案）

下载结果按来源分类存放在 `downloads/pdfs/<publisher>/` 目录下，并会自动严格控制速率以满足各家 TDM 限制。

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e .
```

## 配置

1. 将 Web of Science 导出的 `savedrecs.xls` 放在仓库根目录（或在运行时通过 `--savedrecs` 指定路径）。  
2. 在 `.env` 或环境变量中提供所需凭证/联系方式：

   ```ini
   WILEY_TDM_TOKEN=...
   ELSEVIER_API_KEY=...
   SPRINGER_API_KEY=...          # 可选，仅下载开放获取条目
   CROSSREF_MAILTO=you@example.com
   OPENALEX_MAILTO=you@example.com
   ```

   - 如果缺少某个凭证，相关出版商的内容会被跳过。  
   - Crossref/OpenAlex 至少需要一个 `mailto`（用于 polite requests）。OpenAlex 会优先使用，Crossref 仅在 OpenAlex 失败时作为备用。  
   - Springer API 仅返回开放获取条目；非 OA 内容需通过机构授权或人工处理。

工具默认会优先读取当前目录下的 `.env` 文件。

## 使用

```bash
python -m auto_paper_download --verbose
```

常用参数：

- `--savedrecs`：指定 `savedrecs.xls` 路径。
- `--output-dir`：自定义下载目录（默认 `downloads/pdfs`）。
- `--max-per-publisher`：限制每个出版商下载篇数，用于烟囱测试。
- `--delay`：自定义下载间隔（默认 1.1s，最低 1.0s）。
- `--overwrite`：即使文件已存在也重新下载。
- `--verbose`：输出详细日志，便于排查失败原因。

## 提示

- 非开放获取的 Springer、ACS、RSC 等内容需要出版社提供 TDM 访问或在浏览器中手动获取。  
- 如果遇到 403/Cloudflare 挑战，通常需要使用校内/白名单 IP 或联系出版社开通自动化访问。  
- 可以通过日志快速定位失败原因，并根据需要扩展新的客户端。

## 测试

```bash
pytest
```
