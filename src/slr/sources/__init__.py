from slr.sources.base import Source, SourceResult
from slr.sources.openalex import OpenAlexSource
from slr.sources.europepmc import EuropePMCSource
from slr.sources.pubmed import PubMedSource
from slr.sources.crossref import CrossrefSource

REGISTRY: dict[str, type[Source]] = {
    "openalex": OpenAlexSource,
    "europepmc": EuropePMCSource,
    "pubmed": PubMedSource,
    "crossref": CrossrefSource,
}

# Paid sources — registered lazily so import-time doesn't crash without keys.
def _register_optional() -> None:
    try:
        from slr.sources.scopus import ScopusSource
        REGISTRY["scopus"] = ScopusSource
    except Exception:
        pass
    try:
        from slr.sources.wos import WosSource
        REGISTRY["wos"] = WosSource
    except Exception:
        pass

_register_optional()


def get_source(name: str) -> type[Source]:
    if name not in REGISTRY:
        raise KeyError(f"unknown source: {name}; available: {sorted(REGISTRY)}")
    return REGISTRY[name]


__all__ = [
    "Source", "SourceResult", "OpenAlexSource", "EuropePMCSource",
    "PubMedSource", "CrossrefSource", "REGISTRY", "get_source",
]
