from slr.importers.base import ImportResult, ImportWarning, Importer
from slr.importers.detect import detect_format
from slr.importers.europepmc_csv import EuropePMCCsvImporter
from slr.importers.generic_csv import GenericCsvImporter
from slr.importers.openalex_csv import OpenAlexCsvImporter
from slr.importers.openalex_json import OpenAlexJsonImporter
from slr.importers.pubmed_csv import PubMedCsvImporter
from slr.importers.ris import RisImporter
from slr.importers.scopus_csv import ScopusCsvImporter
from slr.importers.wos_csv import WosCsvImporter

REGISTRY: dict[str, type[Importer]] = {
    "scopus": ScopusCsvImporter,
    "wos": WosCsvImporter,
    "pubmed": PubMedCsvImporter,
    "europepmc": EuropePMCCsvImporter,
    "openalex": OpenAlexCsvImporter,
    "openalex_json": OpenAlexJsonImporter,
    "csv": GenericCsvImporter,
    "ris": RisImporter,
}


def get_importer(name: str) -> type[Importer]:
    if name not in REGISTRY:
        raise KeyError(f"unknown import format: {name}; available: {sorted(REGISTRY)}")
    return REGISTRY[name]


__all__ = [
    "ImportResult", "ImportWarning", "Importer", "REGISTRY", "get_importer", "detect_format",
    "ScopusCsvImporter", "WosCsvImporter", "PubMedCsvImporter",
    "EuropePMCCsvImporter", "OpenAlexCsvImporter", "OpenAlexJsonImporter",
    "GenericCsvImporter", "RisImporter",
]
