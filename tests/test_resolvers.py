from __future__ import annotations

import json

import httpx

from slr.fulltext.resolvers.europepmc import EuropePMCResolver
from slr.fulltext.resolvers.openalex import OpenAlexResolver
from slr.fulltext.resolvers.semantic_scholar import SemanticScholarResolver
from slr.fulltext.resolvers.unpaywall import UnpaywallResolver


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_openalex_oa_url_from_raw():
    raw = {"source": "openalex", "open_access": {"oa_url": "https://example.org/foo.pdf"}}
    record = {"raw_json": json.dumps(raw)}
    r = OpenAlexResolver().resolve(record)
    assert r.url == "https://example.org/foo.pdf"


def test_openalex_no_oa_url():
    raw = {"source": "openalex", "open_access": {}}
    r = OpenAlexResolver().resolve({"raw_json": json.dumps(raw)})
    assert r.url is None
    assert "no OA URL" in (r.error or "")


def test_europepmc_pmcid_url():
    record = {"pmcid": "PMC1234"}
    r = EuropePMCResolver().resolve(record)
    assert r.url == "https://europepmc.org/articles/PMC1234?pdf=render"


def test_europepmc_no_pmcid():
    r = EuropePMCResolver().resolve({"doi": "10.1/x"})
    assert r.url is None


def test_unpaywall_requires_email():
    r = UnpaywallResolver().resolve({"doi": "10.1/x"})
    assert r.url is None
    assert "contact_email" in (r.error or "")


def test_unpaywall_returns_pdf_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "10.1/x" in str(request.url)
        return httpx.Response(200, json={
            "best_oa_location": {"url_for_pdf": "https://example.org/oa.pdf"},
        })
    client = httpx.Client(transport=_mock_transport(handler))
    r = UnpaywallResolver(contact_email="me@example.org", http_client=client).resolve({"doi": "10.1/x"})
    assert r.url == "https://example.org/oa.pdf"


def test_unpaywall_404():
    def handler(req):
        return httpx.Response(404)
    client = httpx.Client(transport=_mock_transport(handler))
    r = UnpaywallResolver(contact_email="me@example.org", http_client=client).resolve({"doi": "10.1/x"})
    assert r.url is None
    assert "not found" in (r.error or "")


def test_semantic_scholar_no_oa_pdf():
    def handler(req):
        return httpx.Response(200, json={"openAccessPdf": {}, "isOpenAccess": False})
    client = httpx.Client(transport=_mock_transport(handler))
    r = SemanticScholarResolver(http_client=client).resolve({"doi": "10.1/x"})
    assert r.url is None


def test_semantic_scholar_returns_url():
    def handler(req):
        return httpx.Response(200, json={"openAccessPdf": {"url": "https://s2.example/a.pdf"}})
    client = httpx.Client(transport=_mock_transport(handler))
    r = SemanticScholarResolver(http_client=client).resolve({"doi": "10.1/x"})
    assert r.url == "https://s2.example/a.pdf"
