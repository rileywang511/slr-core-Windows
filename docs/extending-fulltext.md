# Extending full-text acquisition and conversion

The full-text pipeline has two extension boundaries:

- A **resolver** finds one legitimate candidate PDF URL for a final record.
- A **converter** changes an on-disk PDF into text suitable for screening.

The orchestration layer owns ordering, downloading, PDF validation, evidence
files, cache management, progress, and errors. Implementations must not write
review artifacts directly.

## Add an acquisition resolver

Create a class that subclasses `slr.fulltext.resolvers.base.Resolver` and
implements `resolve(record)`. Return `ResolveResult(url=...)` for one candidate
PDF URL or `ResolveResult(error=...)` for an expected failure. Put useful but
non-sensitive lookup metadata in `raw`. It is saved in
`fetch_attempts.csv:resolver_metadata_json`, with credential-shaped keys
redacted; never put cookies, tokens, or whole authenticated responses there.

```python
from slr.fulltext.resolvers.base import ResolveResult, Resolver


class ExampleRepositoryResolver(Resolver):
    name = "example_repository"

    def resolve(self, record):
        doi = record.get("doi")
        if not doi:
            return ResolveResult(error="no DOI")
        response = self.http.get(f"https://example.invalid/{doi}")
        if response.status_code == 404:
            return ResolveResult(error="not found")
        response.raise_for_status()
        return ResolveResult(url=response.json()["pdf_url"])
```

Install the module in the same environment as `slr`, then configure it without
editing the acquisition loop:

```yaml
fetch:
  custom_resolvers:
    - my_review_extensions.example:ExampleRepositoryResolver
```

The class receives `contact_email`, `api_key`, and the shared `http_client`.
It must not download a file itself: acquisition verifies the PDF magic bytes,
applies the size limit, saves it, and appends the evidence row.

Test resolver behaviour with an `httpx.MockTransport`, including a candidate
URL, not-found result, service error, and a non-PDF download.

## Add a converter

Create a subclass of `ConverterEngine` in `src/slr/fulltext/converters/` and
register it with `@register_converter("your_name")`. It must return converted
body text plus an optional page count, raise a useful error on failure, and
never decide whether a cache entry is valid. The controller in
`src/slr/fulltext/convert.py` owns cache/evidence files. Add a focused test
that stubs the remote/local engine and asserts its evidence row and output path.
