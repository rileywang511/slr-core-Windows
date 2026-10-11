from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import json
import pytest

import slr.fulltext.acquire as acquire_mod
from slr.fulltext.acquire import Acquirer
from slr.config import FetchConfig, ProxyConfig
from slr.record import Record
from slr.fulltext.resolvers.base import ResolveResult, Resolver
from tests import ReviewFiles


_PDF_BYTES = b"%PDF-1.4\n%fake but starts with the right magic\n%%EOF"
_HTML_BYTES = b"<!doctype html><body>not a pdf</body>"


def _mock_handler_for(mapping: dict[str, Any]):
    """mapping: {url-substring: response}, response is (status, content_type, bytes)."""
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        for key, (status, content_type, body) in mapping.items():
            if key in url:
                return httpx.Response(status, headers={"content-type": content_type}, content=body)
        return httpx.Response(404)
    return handler


def _setup_store(tmp_path: Path) -> ReviewFiles:
    store = ReviewFiles(tmp_path)
    store.init()
    return store


def test_acquirer_succeeds_with_unpaywall(tmp_path: Path):
    store = _setup_store(tmp_path)
    # Insert one final record for context.
    run_id = store.insert_source_run("openalex", "q", "tq", [], "0.0.1", 1)
    store.insert_records(run_id, [
        Record(source="openalex", source_id="W1", doi="10.1/x", title="Title",
               raw={"source": "openalex"}),
    ])
    rec_id = store.all_records()[0]["id"]
    store.write_final_records([{
        "canonical_occurrence_id": rec_id, "method": "singleton",
        "members": [{"source_occurrence_id": rec_id, "similarity": 1.0}],
    }])
    record_id = store.final_records()[0]["record_id"]

    handler = _mock_handler_for({
        "api.unpaywall.org": (200, "application/json",
                              b'{"best_oa_location": {"url_for_pdf": "https://example.org/foo.pdf"}}'),
        "example.org/foo.pdf": (200, "application/pdf", _PDF_BYTES),
    })
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    config = FetchConfig(
        sources=["unpaywall"],
        cache_dir="pdfs/",
    )
    acquirer = Acquirer(review_dir=tmp_path, config=config,
                        contact_email="me@example.org", http_client=client)

    records = [{
        "record_id": record_id,
        "doi": "10.1/x",
        "raw_json": '{"source":"openalex"}',
    }]
    attempts = acquirer.acquire_one(records[0])
    assert len(attempts) == 1
    a = attempts[0]
    assert a.pdf_sha256 is not None
    assert a.source == "unpaywall"
    assert (tmp_path / a.pdf_path).exists()


def test_acquirer_rejects_non_pdf(tmp_path: Path):
    handler = _mock_handler_for({
        "api.unpaywall.org": (200, "application/json",
                              b'{"best_oa_location": {"url_for_pdf": "https://example.org/oops.html"}}'),
        "example.org/oops.html": (200, "text/html", _HTML_BYTES),
    })
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    config = FetchConfig(sources=["unpaywall"])
    acquirer = Acquirer(review_dir=tmp_path, config=config,
                        contact_email="me@example.org", http_client=client)
    attempts = acquirer.acquire_one({"record_id": 1, "doi": "10.1/x", "raw_json": "{}"})
    assert attempts[-1].pdf_sha256 is None
    assert "not a PDF" in (attempts[-1].error or "")


def test_acquirer_falls_back_to_second_resolver(tmp_path: Path):
    handler = _mock_handler_for({
        # openalex resolver doesn't make a request — it inspects raw_json
        # unpaywall: 404 -> failure
        "api.unpaywall.org": (404, "application/json", b'{}'),
        # europepmc: success via PMCID
        "europepmc.org/articles/PMC42": (200, "application/pdf", _PDF_BYTES),
    })
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    config = FetchConfig(
        sources=["openalex_oa_url", "unpaywall", "europepmc_oa"],
    )
    acquirer = Acquirer(review_dir=tmp_path, config=config,
                        contact_email="me@example.org", http_client=client)
    attempts = acquirer.acquire_one({
        "record_id": 1,
        "doi": "10.1/x",
        "pmcid": "PMC42",
        "raw_json": '{"source":"openalex","open_access":{}}',  # no OA URL
    })
    # All three resolvers should have been tried; last succeeds
    sources = [a.source for a in attempts]
    assert sources == ["openalex_oa_url", "unpaywall", "europepmc_oa"]
    assert attempts[-1].pdf_sha256 is not None


def test_unknown_resolver_name_is_an_actionable_error(tmp_path: Path):
    acquirer = Acquirer(tmp_path, FetchConfig(sources=["unpaywall_typo"]))

    with pytest.raises(ValueError, match="unknown full-text resolver"):
        acquirer._build_resolvers()


def test_proxy_cookie_jar_is_applied_only_to_proxy_download(tmp_path: Path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(".proxy.example\tTRUE\t/\tTRUE\t0\tsession\tprivate-value\n")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "proxy.example"
        assert request.headers["cookie"] == "session=private-value"
        return httpx.Response(200, content=_PDF_BYTES)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    acquirer = Acquirer(
        tmp_path,
        FetchConfig(
            sources=[],
            proxy=ProxyConfig(
                enabled=True,
                url="https://proxy.example/login?url={target}",
                cookie_jar=str(cookie_file),
            ),
        ),
        http_client=client,
    )

    attempts = acquirer.acquire_one({"record_id": 1, "doi": "10.1/example"})

    assert attempts[-1].pdf_sha256 is not None
    evidence = (tmp_path / "fetch_attempts.csv").read_text()
    assert "private-value" not in evidence
    assert str(cookie_file) not in evidence


def test_resolver_metadata_is_persisted_but_credential_values_are_redacted(tmp_path: Path, monkeypatch):
    class MetadataResolver(Resolver):
        name = "metadata_test"

        def resolve(self, record):
            return ResolveResult(
                url="https://example.org/paper.pdf",
                raw={"lookup_id": "abc", "access_token": "must-not-persist"},
            )

    monkeypatch.setitem(acquire_mod.RESOLVER_REGISTRY, "metadata_test", MetadataResolver)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=_PDF_BYTES)
    ))
    acquirer = Acquirer(tmp_path, FetchConfig(sources=["metadata_test"]), http_client=client)

    acquirer.acquire_one({"record_id": 1, "title": "A paper"})

    rows = acquire_mod.frames.attempts_by_record(tmp_path)[1]
    metadata = json.loads(rows[0]["resolver_metadata_json"])
    assert metadata == {"access_token": "[redacted]", "lookup_id": "abc"}
