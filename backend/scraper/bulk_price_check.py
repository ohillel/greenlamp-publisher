"""
Bulk price checker used by Or's "Price Check" tool.

Given an arbitrary pasted list of URLs/domains, returns the PressWhizz price
(scraped live) plus the Links.me, PRNews and Collaborator prices held in the
synced catalog — see price_catalog.py.

Domains are checked one at a time rather than in parallel: only PressWhizz
still opens a browser, and running several Playwright instances against the
same account at once causes session conflicts where most concurrent logins
fail and return null. One at a time is slower but reliable. The catalog reads
add a single indexed query each.
"""
from .prices import fetch_prices
from .price_catalog import normalize_domain

# Kept so the signature and sheet column order stay stable; Links.me no longer
# looks anything up by project, since it is read from the catalog.
DEFAULT_LINKSME_CLIENT = "apiiro"


def check_prices_bulk(urls: list[str], on_progress=None) -> list[dict]:
    """
    Returns a list of
        {"original_url": <as typed>, "domain": <normalized>,
         "price_presswhizz":   <int|None>,     # USD, live
         "price_linksme":      <float|None>,   # EUR, catalog
         "price_prnews":       <float|None>,   # USD, catalog
         "price_collaborator": <float|None>}   # EUR, catalog
    in the same order as the input. A domain that errors yields None for that
    price rather than failing the whole batch.

    on_progress(done, total) is called after each domain, so a caller running
    this in the background can report how far along it is. A progress callback
    that raises is ignored — reporting must never sink the run itself.
    """
    results = []
    total = len(urls)

    for u in urls:
        domain = normalize_domain(u)
        result = {
            "original_url": u,
            "domain": domain,
            "price_presswhizz": None,
            "price_linksme": None,
            "price_prnews": None,
            "price_collaborator": None,
        }

        try:
            fetched = fetch_prices(domain, DEFAULT_LINKSME_CLIENT)
            result["price_presswhizz"] = fetched.get("presswhizz")
            result["price_linksme"] = fetched.get("linksme")
            result["price_prnews"] = fetched.get("prnews")
            result["price_collaborator"] = fetched.get("collaborator")
        except Exception as e:
            print(f"  [bulk_price_check] error for {domain!r}: {e}")

        results.append(result)
        if on_progress is not None:
            try:
                on_progress(len(results), total)
            except Exception as e:
                print(f"  [bulk_price_check] progress callback failed: {e}")

    return results
