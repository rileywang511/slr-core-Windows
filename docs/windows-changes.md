# Windows adaptation

This version is based on [slr-core](https://github.com/reubenduncan/slr-core),
originally developed by [Reuben Duncan](https://github.com/reubenduncan).
The supplied `slr-core-main` archive matches upstream commit
[`477aec35dcba15cac53681aa91f644467e4e4f5b`](https://github.com/reubenduncan/slr-core/commit/477aec35dcba15cac53681aa91f644467e4e4f5b)
across all 144 files. Original author metadata, package name, version and
documentation are retained. This repository imports the source archive and
does not recreate upstream Git history. See [upstream attribution](upstream.md).

Changes:

- Use Windows byte-range locks for concurrent review writes, keeping `flock`
  for Unix. Locks are released on errors and process exit.
- Explicit UTF-8 text persistence and exports; accept UTF-8 BOM in YAML and
  stored CSV inputs.
- Use UTF-8 for marker worker output and subprocess communication; close child
  process resources after shutdown.
- Store relative PDF and Markdown paths with forward slashes.
- Encode Unicode filenames in the marker HTTP client's metadata header.
- Resolve standard Windows npm Mermaid shims through Node.js and retain JSON
  and Mermaid source when optional SVG rendering fails.
- Add PowerShell setup instructions and Windows/Linux CI configurations.
- Add native process-lock, Unicode filename, export and CLI workflow tests;
  isolate local HTTP tests from machine proxy settings.

Validation on Windows, Python 3.11:

```powershell
.\.venv\Scripts\python.exe -X utf8=0 -m pytest
```

Result: 219 passed, 2 skipped because the optional OpenAI SDK was not installed.
CLI version/help and fatal-error static checks also passed. Local OCR/GPU
engines and a real Mermaid installation were not exercised; their orchestration
is covered with fake converters and renderer tests.

Source packages exclude virtual environments, caches, build outputs and the
local `researcher-mobility` review directory. See [Windows setup](windows.md)
for installation and usage.
