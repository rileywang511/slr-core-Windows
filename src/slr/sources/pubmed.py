"""PubMed source via NCBI E-utilities.

The source searches PMIDs with ESearch, then enriches them with EFetch XML so
records include abstracts and stable article identifiers. If EFetch fails for a
batch, it falls back to ESummary and returns a prominent warning rather than
quietly claiming complete metadata.
"""

from __future__ import annotations

import os
import time
from typing import Any
from xml.etree import ElementTree

import httpx

from slr.config import HttpConfig
from slr.query.ast import Expr
from slr.query.emitters.pubmed import PubMedEmitter
from slr.record import Author, Record
from slr.sources.base import Source, SourceResult


ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


class PubMedSource(Source):
    name = "pubmed"

    def __init__(self, contact_email: str | None = None, http_client: httpx.Client | None = None,
                 api_key: str | None = None, http_config: HttpConfig | None = None):
        super().__init__(contact_email=contact_email, http_client=http_client, http_config=http_config)
        if self.http is None:
            self.http = httpx.Client(timeout=30.0)
        self.api_key = api_key or os.environ.get("PUBMED_API_KEY") or os.environ.get("NCBI_API_KEY")

    def _common_params(self) -> dict[str, str]:
        params: dict[str, str] = {"tool": "slr-agent"}
        if self.contact_email:
            params["email"] = self.contact_email
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def search(self, expr: Expr, date_from: str | None = None, date_to: str | None = None,
               limit: int | None = None, progress=None) -> SourceResult:
        emitter = PubMedEmitter()
        emitted = emitter.emit(expr, date_from=date_from, date_to=date_to)
        ids: list[str] = []
        retstart = 0
        retmax = 200
        n_total: int | None = None
        page = 0
        on_retry = (lambda ev: progress({**ev, "source": self.name})) if progress else None
        while True:
            params = {
                **self._common_params(),
                "db": "pubmed",
                "term": emitted.query_string,
                "retmode": "json",
                "retstart": str(retstart),
                "retmax": str(retmax),
            }
            resp = self.request("GET", ESEARCH_URL, params=params, on_event=on_retry)
            payload = resp.json()
            esr = payload.get("esearchresult") or {}
            batch = esr.get("idlist") or []
            ids.extend(batch)
            page += 1
            if n_total is None:
                try:
                    n_total = int(esr.get("count") or 0)
                except (TypeError, ValueError):
                    n_total = None
            if progress:
                progress({"source": self.name, "phase": "esearch", "page": page,
                          "n_so_far": len(ids), "n_total": n_total, "limit": limit})
            if limit is not None and len(ids) >= limit:
                ids = ids[:limit]
                break
            total = int(esr.get("count") or 0)
            retstart += len(batch)
            if retstart >= total or not batch:
                break
            time.sleep(0.34)  # 3 req/sec without key

        # Enrich IDs with EFetch. ESummary remains a deliberately warned
        # fallback, so a failed metadata request does not silently discard IDs.
        raw_records: list[dict[str, Any]] = []
        warnings = list(emitted.warnings)
        chunk_idx = 0
        for chunk_start in range(0, len(ids), 200):
            chunk = ids[chunk_start:chunk_start + 200]
            if not chunk:
                break
            params = {
                **self._common_params(),
                "db": "pubmed",
                "id": ",".join(chunk),
                "retmode": "xml",
            }
            try:
                resp = self.request("GET", EFETCH_URL, params=params, on_event=on_retry)
                fetched = _parse_efetch_xml(resp.text)
                if not fetched:
                    raise ValueError("response contained no PubmedArticle records")
                raw_records.extend(fetched)
                phase = "efetch"
            except (httpx.HTTPError, ElementTree.ParseError, ValueError) as exc:
                warnings.append(
                    "EFetch failed for "
                    f"{len(chunk)} PubMed record(s): {exc}. "
                    "Fell back to ESummary; abstracts and some identifiers may be missing."
                )
                raw_records.extend(self._fetch_summaries(chunk, on_retry))
                phase = "esummary fallback"
            chunk_idx += 1
            if progress:
                progress({"source": self.name, "phase": phase, "page": chunk_idx,
                          "n_so_far": len(raw_records), "n_total": len(ids), "limit": limit})
            time.sleep(0.34)

        return SourceResult(
            raw_records=raw_records,
            query_translated=emitted.query_string,
            warnings=warnings,
        )

    def _fetch_summaries(self, ids: list[str], on_retry) -> list[dict[str, Any]]:
        """Compatibility fallback for a failed EFetch batch (always warned)."""
        params = {
            **self._common_params(),
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "json",
        }
        resp = self.request("GET", ESUMMARY_URL, params=params, on_event=on_retry)
        result = resp.json().get("result") or {}
        return [result[pmid] for pmid in ids if result.get(pmid)]

    def normalize(self, raw: dict[str, Any]) -> Record:
        pmid = raw.get("uid") or raw.get("pmid") or ""
        article_ids = raw.get("articleids") or []
        doi = None
        pmcid = None
        for aid in article_ids:
            idtype = aid.get("idtype")
            if idtype == "doi":
                doi = aid.get("value")
            elif idtype == "pmc":
                pmcid = aid.get("value")
        year = None
        pubdate = raw.get("pubdate") or ""
        if pubdate:
            try:
                year = int(pubdate.split(" ")[0])
            except ValueError:
                year = None
        authors = []
        for a in raw.get("authors") or []:
            name = a.get("name") or ""
            parts = name.split()
            family = parts[0] if parts else None
            given = " ".join(parts[1:]) if len(parts) > 1 else None
            authors.append(Author(family=family, given=given))
        return Record(
            source="pubmed",
            source_id=str(pmid),
            doi=doi,
            title=raw.get("title") or "",
            abstract=raw.get("abstract"),
            authors=authors,
            year=year,
            venue=raw.get("fulljournalname") or raw.get("source"),
            type=(raw.get("pubtype") or [None])[0] if raw.get("pubtype") else None,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            pmid=str(pmid),
            pmcid=pmcid,
            raw=raw,
        )


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def _parse_efetch_xml(xml_text: str) -> list[dict[str, Any]]:
    """Map EFetch's XML response to the stable shape used by ``normalize``."""
    root = ElementTree.fromstring(xml_text)
    records: list[dict[str, Any]] = []
    for article_node in root.findall("./PubmedArticle"):
        citation = article_node.find("./MedlineCitation")
        article = citation.find("./Article") if citation is not None else None
        pmid = _text(citation.find("./PMID")) if citation is not None else None
        if article is None or not pmid:
            continue

        article_ids = [
            {"idtype": identifier.get("IdType", "").lower(), "value": _text(identifier)}
            for identifier in article_node.findall("./PubmedData/ArticleIdList/ArticleId")
            if _text(identifier)
        ]
        if not any(item["idtype"] == "doi" for item in article_ids):
            article_ids.extend(
                {"idtype": "doi", "value": _text(identifier)}
                for identifier in article.findall("./ELocationID[@EIdType='doi']")
                if _text(identifier)
            )

        abstract_parts = []
        for part in article.findall("./Abstract/AbstractText"):
            text = _text(part)
            if text:
                label = part.get("Label")
                abstract_parts.append(f"{label}: {text}" if label else text)
        authors = []
        for author in article.findall("./AuthorList/Author"):
            collective = _text(author.find("./CollectiveName"))
            family = _text(author.find("./LastName"))
            given = _text(author.find("./ForeName")) or _text(author.find("./Initials"))
            name = collective or " ".join(part for part in (family, given) if part)
            if name:
                authors.append({"name": name})

        year = (
            _text(article.find("./Journal/JournalIssue/PubDate/Year"))
            or _text(article.find("./ArticleDate/Year"))
            or _text(citation.find("./DateCompleted/Year"))
        )
        records.append({
            "uid": pmid,
            "articleids": article_ids,
            "title": _text(article.find("./ArticleTitle")) or "",
            "abstract": "\n\n".join(abstract_parts) or None,
            "authors": authors,
            "pubdate": year or "",
            "fulljournalname": _text(article.find("./Journal/Title")),
            "source": _text(article.find("./Journal/ISOAbbreviation")),
            "pubtype": [
                text for node in article.findall("./PublicationTypeList/PublicationType")
                if (text := _text(node))
            ],
        })
    return records
