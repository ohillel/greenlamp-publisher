"""
Bulk price checker used by Or's "Price Check" tool.

Given an arbitrary pasted list of URLs/domains, fetches PressWhizz +
Links.me prices for each (Links.me always looked up under the "apiiro"
catalog, since these are ad-hoc lookups not tied to a specific client),
plus PRNews.io and Collaborator.pro prices in EUR.

Does not touch the per-article scraping flow in prices.py: PRNews and
Collaborator are called directly from here, so fetch_prices() — which the
per-article path also uses — keeps checking exactly the two sites it
always has.

Sites are checked sequentially (not in parallel) — running multiple
Playwright instances against the same account at once causes session
conflicts, where most concurrent logins fail and return null. One site at a
time is slower but reliable. Adding the two EUR sources roughly doubles the
time per domain.
"""
from .prices import fetch_prices
from .linksme import _normalize_domain
from . import prnews, collaborator

DEFAULT_LINKSME_CLIENT = "apiiro"


def check_prices_bulk(urls: list[str]) -> list[dict]:
    """
    Returns a list of
        {"original_url": <as typed>, "domain": <normalized>,
         "price_presswhizz":   <int|None>,     # USD, as before
         "price_linksme":      <int|None>,     # USD, as before
         "price_prnews":       <float|None>,   # EUR
         "price_collaborator": <float|None>}   # EUR
    in the same order as the input. A site that errors yields None for that
    price rather than failing the whole batch, and one failing source never
    prevents the others from being recorded.
    """
    domains = [_normalize_domain(u) for u in urls]
    results = []

    for u, domain in zip(urls, domains):
        result = {
            "original_url": u,
            "domain": domain,
            "price_presswhizz": None,
            "price_linksme": None,
            "price_prnews": None,
            "price_collaborator": None,
        }

        # PressWhizz + Links.me, via the existing orchestrator.
        try:
            fetched = fetch_prices(domain, DEFAULT_LINKSME_CLIENT)
            result["price_presswhizz"] = fetched.get("presswhizz")
            result["price_linksme"] = fetched.get("linksme")
        except Exception as e:
            print(f"  [bulk_price_check] presswhizz/linksme error for {domain!r}: {e}")

        # The EUR sources, each isolated so one failure cannot lose the other.
        for label, key, fn in (
            ("prnews",       "price_prnews",       prnews.get_price),
            ("collaborator", "price_collaborator", collaborator.get_price),
        ):
            try:
                result[key] = fn(domain)
            except Exception as e:
                print(f"  [bulk_price_check] {label} error for {domain!r}: {e}")

        results.append(result)

    return results
