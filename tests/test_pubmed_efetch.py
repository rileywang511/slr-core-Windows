from __future__ import annotations

import httpx

from slr.config import HttpConfig
from slr.query import parse
from slr.sources.pubmed import PubMedSource


EFETCH_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345</PMID>
      <Article>
        <Journal><ISOAbbreviation>J Test</ISOAbbreviation><Title>Journal of Tests</Title>
          <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue>
        </Journal>
        <ArticleTitle>Paper &amp; evidence</ArticleTitle>
        <Abstract><AbstractText Label="BACKGROUND">First part.</AbstractText><AbstractText>Second part.</AbstractText></Abstract>
        <AuthorList><Author><LastName>Smith</LastName><ForeName>Jane</ForeName></Author></AuthorList>
        <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/test</ArticleId><ArticleId IdType="pmc">PMC123</ArticleId></ArticleIdList></PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_pubmed_uses_efetch_for_rich_metadata(monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": ["12345"]}})
        if request.url.path.endswith("efetch.fcgi"):
            return httpx.Response(200, text=EFETCH_XML)
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr("slr.sources.pubmed.time.sleep", lambda _: None)
    source = PubMedSource(http_client=_client(handler), http_config=HttpConfig(max_retries=0))
    result, records = source.search_and_normalize(parse("TS=(crispr)"))

    assert any(path.endswith("efetch.fcgi") for path in calls)
    assert not any(path.endswith("esummary.fcgi") for path in calls)
    assert result.warnings == []
    record = records[0]
    assert record.title == "Paper & evidence"
    assert record.abstract == "BACKGROUND: First part.\n\nSecond part."
    assert record.doi == "10.1000/test"
    assert record.pmcid == "PMC123"
    assert record.year == 2024
    assert record.authors[0].family == "Smith"


def test_pubmed_efetch_failure_warns_and_falls_back_to_esummary(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": ["12345"]}})
        if request.url.path.endswith("efetch.fcgi"):
            return httpx.Response(500, text="NCBI unavailable")
        if request.url.path.endswith("esummary.fcgi"):
            return httpx.Response(200, json={"result": {"12345": {
                "uid": "12345", "title": "Summary fallback", "pubdate": "2024 Jan",
                "articleids": [], "authors": [], "fulljournalname": "Journal",
            }}})
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr("slr.sources.pubmed.time.sleep", lambda _: None)
    source = PubMedSource(http_client=_client(handler), http_config=HttpConfig(max_retries=0))
    result, records = source.search_and_normalize(parse("TS=(crispr)"))

    assert len(records) == 1
    assert records[0].title == "Summary fallback"
    assert records[0].abstract is None
    assert len(result.warnings) == 1
    assert "EFetch failed" in result.warnings[0]
    assert "Fell back to ESummary" in result.warnings[0]
