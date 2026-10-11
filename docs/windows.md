# Windows setup

Use Python 3.11 or newer. Run these commands in PowerShell from the project
directory (the directory containing `pyproject.toml`):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[llm]"
.\.venv\Scripts\slr.exe version
.\.venv\Scripts\slr.exe init "my-review"
notepad "my-review\protocol.yaml"
notepad "my-review\config.yaml"
```

The explicit executable paths work without activating the environment or
changing PowerShell's execution policy. Install `.` instead of `.[llm]` for
the core commands without provider SDKs. If you already use uv, run
`uv venv --python 3.11` and `uv pip install -e ".[llm]"` instead.

Edit the protocol before starting the review. Set credentials in the current
PowerShell session as needed:

```powershell
$env:OPENAI_API_KEY = "your-key"
# Or: $env:ANTHROPIC_API_KEY = "your-key"
.\.venv\Scripts\slr.exe import "my-review" "C:\Downloads\scopus-export.csv"
.\.venv\Scripts\slr.exe dedup "my-review"
.\.venv\Scripts\slr.exe screen "my-review" --stage title_abstract
.\.venv\Scripts\slr.exe fetch "my-review"
.\.venv\Scripts\slr.exe screen "my-review" --stage full_text --converter none
.\.venv\Scripts\slr.exe prisma "my-review"
.\.venv\Scripts\slr.exe export "my-review" --format ris --stage included
```

`none` sends PDFs to a PDF-capable model. Hosted conversion also works with
`--converter datalab` and `$env:DATALAB_API_KEY`, or `--converter extend` and
`$env:EXTEND_API_KEY`. These converters do not need local OCR models.
For a self-hosted marker server, set `$env:MARKER_SERVER_URL` and optionally
`$env:MARKER_SERVER_KEY`, then use `--converter server`.

Local converters are optional dependencies: install `.[marker]`,
`.[pdf-inspector]`, or `.[anydoc]` for the selected engine. Their model,
OCR and GPU runtimes require separate setup; the core Windows test suite
uses fake converters and does not validate those external runtimes.

Quote paths containing spaces. Save protocol and configuration files as UTF-8;
UTF-8 BOM files from Windows editors are accepted. In YAML, use forward slashes
or single quotes for Windows paths so backslashes are not escape sequences:

```yaml
fetch:
  cache_dir: pdfs/
  markdown_dir: markdown/
  proxy:
    cookie_jar: 'C:\Users\reviewer\Downloads\cookies.txt'
```

CSV, RIS, JSONL and converted text use UTF-8 on every platform. When opening
a CSV in Excel, use **Data → From Text/CSV** and select UTF-8 if Excel does
not detect it automatically. Stored relative PDF and Markdown paths use `/`,
which Windows accepts and which also makes review directories portable.

The per-review writer lock uses Windows byte-range locking. The
`.slr.write.lock` file can remain after a command finishes; its presence does
not indicate a held lock. The operating system releases locks when a process
exits. Concurrent mutating commands for the same review are rejected.

PRISMA always produces JSON and Mermaid source. Optional SVG rendering needs
Node.js and `mmdc` on PATH. Standard global and local npm Windows shims are
supported by invoking their JavaScript entry point directly. If the renderer
is missing or fails, JSON and Mermaid output remain available.

To run the development checks:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -m "not slow"
```
