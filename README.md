# slr-agent

> **Windows adaptation of [slr-core](https://github.com/reubenduncan/slr-core),
> originally developed by [Reuben Duncan](https://github.com/reubenduncan).**
> This version is maintained by [rileywang511](https://github.com/rileywang511)
> and adds Windows compatibility, Unicode handling, setup documentation and tests.
> Thanks to the original author for the underlying project.

本项目基于 Reuben Duncan 的 **slr-core** 修改，主要增加 Windows 兼容性、
中文编码支持和相关测试。原始源码和项目设计归原作者及上游贡献者所有。
来源版本与许可说明见 [Upstream attribution](docs/upstream.md)，
适配内容见 [Windows changes](docs/windows-changes.md)。

Reproducible, PRISMA-compliant systematic literature review CLI.

`slr` takes a single WoS-style search query and executes it across multiple
bibliographic databases (OpenAlex, Europe PMC, PubMed, Crossref, optionally
Scopus / Web of Science). It deduplicates the combined result set, optionally
screens records with a rule-based or LLM-based screener, downloads open-access
PDFs, and screens the full text — converting each PDF with
[marker-pdf](https://github.com/datalab-to/marker) locally, the
[Firecrawl pdf-inspector](https://github.com/firecrawl/pdf-inspector) or
[Firecrawl AnyDoc](https://github.com/firecrawl/anydoc) locally, the
[Datalab](https://www.datalab.to) or [Extend Parse 2.0](https://docs.extend.ai)
APIs, your own self-hosted marker server (`slr marker-server`), or sending the
raw PDF to a PDF-capable model — and produces an audit
trail rich enough that **any third party can reconstruct
the full PRISMA flow diagram and every methodological step from the output
directory alone**.

Already have CSV / RIS exports from a database's web UI? `slr import` ingests
Scopus, Web of Science, PubMed, Europe PMC, generic CSV, and RIS files into
the same pipeline.

Live PubMed searches use NCBI EFetch to collect abstracts and article
identifiers. If an EFetch batch fails, `slr search` writes a visible warning
and uses ESummary metadata for that batch rather than silently losing it.

## Install

```bash
uv pip install -e .                  # core: search + dedup + prisma + exports
uv pip install -e '.[llm]'           # add Anthropic + OpenAI provider SDKs
uv pip install -e '.[marker]'        # add marker-pdf for converter: marker (heavy: torch + models)
uv pip install -e '.[pdf-inspector]' # local, layout-aware PDF -> Markdown
uv pip install -e '.[anydoc]'        # local Firecrawl AnyDoc PDF -> Markdown
uv pip install -e '.[all,dev]'       # full install + dev tools
# converter: extend / datalab need no extra install — just set EXTEND_API_KEY / DATALAB_API_KEY
```

## Quick start

For Windows installation and PowerShell commands, see
[Windows setup](docs/windows.md).

```bash
slr init my-review
$EDITOR my-review/protocol.yaml      # set the review question, query, sources, and criteria
$EDITOR my-review/config.yaml        # optionally set contact email, LLM, and full-text settings

slr search my-review                         # or import database exports instead:
slr import my-review scopus-export.csv       # ingest pre-downloaded exports
slr import my-review wos-export.csv
slr dedup my-review
slr screen my-review --stage title_abstract
slr fetch my-review
slr screen my-review --stage full_text       # converts PDFs inline: --converter marker|pdf-inspector|anydoc|extend|datalab|server|none
slr prisma my-review
slr export my-review --format ris --stage included
slr export-included my-review                # CSV of full-text-passed studies + paths/reasoning/provenance
```

`slr inspect my-review --json` reports which stages are completed and the next
suggested command — agents should call it before deciding what to run next.

## Configure a review

`protocol.yaml` is the shareable methodological declaration: query, sources,
criteria, screening approach, and deduplication threshold. `config.yaml` is
machine-specific runtime plumbing, organised around the commands that use it:
`search`, `screen`, and `fetch`. Both files reject unknown keys, duplicate
sources, duplicate criterion IDs, and invalid date ranges so a misspelling
cannot become inert configuration.

```yaml
# protocol.yaml
sources: [openalex, pubmed, europepmc]
screen:
  title_abstract: { type: llm }
  full_text: { type: llm, fulltext: required }

# config.yaml — active settings are grouped by command
search:
  contact_email: reviewer@example.org       # also used by OA resolvers during fetch
  http: { max_retries: 5, base_delay: 1.0, max_delay: 60.0 }
screen:
  title_abstract: { provider: anthropic, model: claude-sonnet-4-6 }
  full_text: { provider: openai-compatible, base_url: http://localhost:11434/v1,
               model: qwen2.5:32b, converter: marker }
fetch:
  sources: [openalex_oa_url, unpaywall, europepmc_oa]
```

Supported live sources are OpenAlex, Europe PMC, PubMed, Crossref, Scopus, and
Web of Science. Scopus and Web of Science require the relevant credentials and
are also supported through exported CSV. PubMed EFetch failures are printed as
warnings and leave the source run reviewable rather than silently dropping
records.

### Full-text screening policy

The `screen.full_text.fulltext` setting defines what evidence the stage may
use after title/abstract inclusion:

| Value | Behaviour |
| --- | --- |
| `required` | Screen only records with a valid PDF. |
| `optional` | Use a PDF when one exists; otherwise screen the bibliographic record and abstract. |
| `ignored` | Deliberately screen abstract-only; this stage neither scans nor converts PDFs. |

`slr screen --stage full_text` performs conversion inline. Use `--converter`
to override the configured `marker`, `pdf-inspector`, `anydoc`, `extend`,
`datalab`, `server`, or `none` converter for one run. `pdf-inspector` is the
fast local choice for layout-aware native-text extraction and can selectively
OCR scanned pages when configured; `anydoc` is Firecrawl's local, consistent
document-to-Markdown serializer (native text only). `none` passes the raw PDF
to a PDF-capable model.

## How it works

A "review" is a directory. Every command reads and writes inside it, appending
to `audit.log.jsonl`. The directory is the unit of reproducibility — share it
to share the full provenance.

Each pipeline stage reads and writes named CSV/JSON artifacts, so all
intermediate state is plain, inspectable, diffable text — no binary database.
`config.yaml` is strict: every documented setting is consumed by the runtime,
and unknown or retired keys are rejected rather than silently ignored. Commands
without durable machine settings, such as `import`, `dedup`, and `export`, have
no empty config section: their reproducible choices belong in `protocol.yaml`
or are explicit command options.

```
my-review/
├── protocol.yaml               # human-authored query, sources, criteria
├── audit.log.jsonl             # append-only log of every action + tool version
├── records.csv                 # one sanitised, deduplicated paper per row
├── evidence.jsonl              # append-only source runs, raw rows, warnings, errors, dedup decisions
├── screen_title_abstract.csv   # TA decisions (appended one at a time)
├── screen_full_text.csv        # full-text decisions
├── fetch_attempts.csv          # OA resolver attempts per final record
├── fulltext_conversions.csv    # PDF -> text conversions per final record
├── _state.json                 # per-stage completion / input+output hashes
├── pdfs/    markdown/          # downloaded PDFs and cached converted text
├── prisma.json/.mmd/.svg       # PRISMA counts and flow diagram
└── exports/                    # included.ris, all.jsonl, screened.csv
```

Stages declare their dependencies. If a completed stage is re-run with changed
inputs or output, its downstream completion markers and derived artifacts are
invalidated, so `inspect` guides the review back through only the affected work.
Prompt-forming methodology (review title and inclusion/exclusion criteria) is
part of an LLM screening stage's fingerprint, so changing it invalidates stale
decisions rather than resuming them.

The names used throughout the product are deliberately narrow: a **source
occurrence** is one citation imported from one source, and a **final record**
is one sanitised, deduplicated paper in `records.csv`. A final record's
`record_id` keys screening, acquisition, conversion, exports, and filenames.
`evidence.jsonl` preserves the evidence behind each final record: source-run
metadata, each normalised occurrence with its original payload, one warning per
unparseable import row (including that row), operational errors, the active
source snapshot, and every DOI/fuzzy match decision and score. It is
append-only: current results are reconstructed from the latest snapshot and
deduplication run, while historical evidence remains reviewable.

Reviews created by older releases are migrated to the ledger on first access;
their original evidence files are left untouched for comparison. New reviews
create only `records.csv` and `evidence.jsonl` for ingestion and deduplication.

Writes that replace a review artifact use an atomic replacement; mutating `slr`
commands also take a per-review writer lock. Decision and audit rows are flushed
as they are appended, so a resumed screening run does not repeat completed
records after an interruption.

See [SKILL.md](SKILL.md) for the full usage guide (also installable as a
Claude Code skill).

## Reproducibility & PRISMA

- The exact query sent to each source and any translation warnings (e.g. lossy
  `NEAR/n` mapping to AND) are stored in `evidence.jsonl`.
- Every dedup merge records its method and similarity score. DOI-missing source
  records may join an equivalent DOI-bearing paper through a fuzzy
  title+author+year match; records with different DOIs are never fuzzy-merged.
- Every screening decision records the screener id (e.g. `claude-sonnet-4-6`,
  `rules-v1`) and reason code from the protocol's criteria.
- Every PDF download attempt records every resolver tried (success or fail).
- Every LLM decision records the model ID, rendered prompt, raw response,
  input/output tokens, and latency in its screening CSV.
- `slr prisma` aggregates everything into `prisma.json` plus a Mermaid diagram.

## On full-text acquisition

Only legitimate open-access sources are queried (OpenAlex, Unpaywall, Europe PMC,
Semantic Scholar, CORE, BASE, OpenAIRE), plus an optional institutional proxy
configurable in `config.yaml`. Sci-Hub and other piracy mirrors are not used.

For papers no OA resolver can reach, `slr request-reprints` produces a CSV with
corresponding-author info and a templated email body the user can send manually.

Resolver attempts are retained in `fetch_attempts.csv`, including a sanitized
`resolver_metadata_json` lookup record. Cookie-, token-, password-, secret-,
and authorization-shaped metadata is redacted before persistence. The optional
institutional proxy accepts a browser-exported Netscape/Mozilla cookie jar; its
cookies stay in memory and are attached only to the proxy request, never to
review artifacts.

Resolvers and converters are extension points rather than one-off command
branches. Register a custom resolver with `fetch.custom_resolvers`, or add a
`ConverterEngine` with `@register_converter`. The controller owns downloading,
PDF validation, cache hashing, artifact writes, and evidence rows. See
[the full extension guide](docs/extending-fulltext.md) for the required
contracts and tests.

## Commands at a glance

| Command | Purpose |
| --- | --- |
| `slr init` | Create a review directory with commented protocol and config examples. |
| `slr search` / `slr import` | Acquire live records or ingest CSV/RIS exports into immutable evidence. |
| `slr dedup` | Produce final records and append reviewable merge decisions to the evidence ledger. |
| `slr screen` | Run a rules or LLM screening stage; resumes from its decision CSV. |
| `slr fetch` | Try the configured open-access resolver chain and write unresolved records for manual collection. |
| `slr prisma` / `slr export` | Produce PRISMA artifacts and RIS, CSV, or JSONL outputs. |
| `slr query-assist` / `slr llm test` | Validate or generate a WoS-style query with the configured LLM. |
| `slr inspect` | Report pipeline state and the next suggested command. |

## License

MIT.

The upstream README declares MIT. At the verified upstream revision, no
standalone `LICENSE` file is present. This adaptation preserves the upstream
declaration and author metadata; see [the upstream license note](docs/upstream.md#license-note).
