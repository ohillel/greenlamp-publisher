"""
Orchestrator: fetch prices from both PressWhizz and Links.me concurrently.
"""
import concurrent.futures
from . import presswhizz, linksme


def fetch_prices(magazine_domain: str, client_name: str, debug: bool = False) -> dict:
    """
    Returns:
        {
            "presswhizz": <int or None>,
            "linksme":    <int or None>,
            "errors":     { "presswhizz": "...", "linksme": "..." }   # only on failure
        }
    """
    result = {"presswhizz": None, "linksme": None, "errors": {}}

    def run_presswhizz():
        return presswhizz.get_price(magazine_domain, debug=debug)

    def run_linksme():
        return linksme.get_price(magazine_domain, client_name, debug=debug)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        pw_future = executor.submit(run_presswhizz)
        lm_future = executor.submit(run_linksme)

        for site, future in [("presswhizz", pw_future), ("linksme", lm_future)]:
            try:
                result[site] = future.result()
            except Exception as e:
                result["errors"][site] = str(e)
                print(f"  [{site}] ERROR: {e}")

    if not result["errors"]:
        del result["errors"]

    return result
