from slr.fulltext.resolvers.base import Resolver, ResolveResult
from slr.fulltext.resolvers.openalex import OpenAlexResolver
from slr.fulltext.resolvers.unpaywall import UnpaywallResolver
from slr.fulltext.resolvers.europepmc import EuropePMCResolver
from slr.fulltext.resolvers.semantic_scholar import SemanticScholarResolver
from slr.fulltext.resolvers.core import CoreResolver
from slr.fulltext.resolvers.base_search import BaseResolver as BielefeldResolver
from slr.fulltext.resolvers.openaire import OpenAIREResolver
from slr.fulltext.resolvers.proxy import ProxyResolver

RESOLVER_REGISTRY: dict[str, type[Resolver]] = {
    "openalex_oa_url": OpenAlexResolver,
    "unpaywall": UnpaywallResolver,
    "europepmc_oa": EuropePMCResolver,
    "semantic_scholar": SemanticScholarResolver,
    "core": CoreResolver,
    "base": BielefeldResolver,
    "openaire": OpenAIREResolver,
    "proxy": ProxyResolver,
}


def load_custom_resolver(spec: str) -> type[Resolver]:
    """Load an explicitly configured ``package.module:ResolverClass``.

    Third-party resolvers use this documented extension path; no change to the
    acquisition loop or this registry is required.
    """
    import importlib

    if ":" not in spec:
        raise ValueError(
            f"invalid custom resolver {spec!r}; use 'package.module:ResolverClass'"
        )
    module_name, class_name = spec.split(":", 1)
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"could not load custom resolver {spec!r}: {exc}") from exc
    if not isinstance(cls, type) or not issubclass(cls, Resolver):
        raise ValueError(f"custom resolver {spec!r} must subclass Resolver")
    return cls

__all__ = ["Resolver", "ResolveResult", "RESOLVER_REGISTRY", "load_custom_resolver"]
