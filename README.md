# Auto Paper Download

`auto-paper-download` 提供一个可独立部署的命令行工具，用于从 Web of Science 导出的 `savedrecs.xls` 文件中提取 DOI，识别对应的出版商（目前支持 Wiley 与 Elsevier），并遵守每秒 1 篇 PDF 的速率限制调用出版商的 Text & Data Mining (TDM) 接口批量下载论文 PDF。

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e .
```

## 准备工作

1. 将 Web of Science 导出的 `savedrecs.xls` 放在仓库根目录，或通过命令行参数指定其他路径。
2. 配置 `.env` 或环境变量以提供各出版商的 API 凭证：
   - `WILEY_TDM_TOKEN`
   - `ELSEVIER_API_KEY`

工具会优先加载当前目录下的 `.env` 文件（可选）。

## 使用方法

```bash
auto-paper-download --output-dir downloads/pdfs
```

可选参数：

- `--savedrecs`：自定义 `savedrecs.xls` 路径
- `--max-per-publisher`：限制每个出版商的下载篇数，便于快速验证
- `--delay`：自定义下载间隔，默认 1.1 秒以满足每秒 1 篇 PDF 的限制
- `--verbose`：输出更详细的调试日志

成功下载的 PDF 会按出版商分类存放在输出目录，例如 `downloads/pdfs/wiley/`。

## 测试

```bash
pytest
```
