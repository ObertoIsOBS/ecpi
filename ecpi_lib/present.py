"""
Present search results, explain options, and ask for confirmation.
"""

from typing import Optional

PAGE_SIZE = 10


def _print_page(results: list[dict], query: str, page: int, page_size: int) -> None:
    """Print a single page of results (1-based page index)."""
    total = len(results)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_results = results[start:end]
    print(f"\nSearch results for '{query}' (page {page}/{total_pages}, {total} total):\n")
    for i, r in enumerate(page_results, start=start + 1):
        src = r.get("source", r.get("repo", ""))
        desc = (r.get("description") or "")[:70]
        if len((r.get("description") or "")) > 70:
            desc += "..."
        print(f"  {i}. [{r['manager']}] {r['name']}")
        if src:
            print(f"      Source: {src}")
        if desc:
            print(f"      {desc}")
        print()


def print_search_results(results: list[dict], query: str) -> None:
    """Print first page of results (for compatibility)."""
    _print_page(results, query, 1, PAGE_SIZE)


def paginated_search_only(results: list[dict], query: str, page_size: int = PAGE_SIZE) -> None:
    """Interactive paginated browse: show 10 at a time, [n]ext [p]rev [q]uit."""
    total = len(results)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = 1
    while True:
        _print_page(results, query, page, page_size)
        prompt = "[n]ext [p]rev [q]uit: " if total_pages > 1 else "[q]uit: "
        try:
            choice = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if not choice or choice == "q":
            return
        if choice == "n" and page < total_pages:
            page += 1
        elif choice == "p" and page > 1:
            page -= 1


def paginated_select(
    results: list[dict],
    query: str,
    page_size: int = PAGE_SIZE,
) -> Optional[dict]:
    """
    Show results 10 per page; user selects by global index (1..len) or n/p/q.
    Returns the chosen result dict or None.
    """
    total = len(results)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = 1
    while True:
        _print_page(results, query, page, page_size)
        prompt = "Select # to install, [n]ext [p]rev [q]uit: "
        try:
            raw = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            continue
        if raw == "q":
            return None
        if raw == "n":
            if page < total_pages:
                page += 1
            continue
        if raw == "p":
            if page > 1:
                page -= 1
            continue
        try:
            n = int(raw)
            if 1 <= n <= total:
                return results[n - 1]
        except ValueError:
            pass


def explain_and_confirm(
    results: list[dict],
    query: str,
    skip_confirm: bool = False,
    page_size: int = PAGE_SIZE,
) -> Optional[dict]:
    """
    Explain which option will be used (or let user choose), then confirm.
    Uses pagination when there are multiple results.
    Returns the chosen result dict or None if cancelled.
    """
    if not results:
        return None

    # If single result, explain and confirm
    if len(results) == 1:
        r = results[0]
        print(f"One match found: {r['name']} (via {r['manager']}).")
        print(f"  Description: {r.get('description', 'N/A')}")
        print(f"  Install command: {r['bin']} ... {r['name']}")
        if skip_confirm:
            return r
        try:
            ans = input("Install this package? [Y/n] ").strip().lower()
            if ans and ans != "y" and ans != "yes":
                return None
            return r
        except (EOFError, KeyboardInterrupt):
            return None

    # Multiple results: paginated selection
    r = paginated_select(results, query, page_size=page_size)
    if r is None:
        return None
    print(f"Selected: {r['name']} (via {r['manager']})")
    print(f"  Command: {r['bin']} ... {r['name']}")
    if skip_confirm:
        return r
    try:
        ans = input("Proceed with install? [Y/n] ").strip().lower()
        if ans and ans != "y" and ans != "yes":
            return None
        return r
    except (EOFError, KeyboardInterrupt):
        return None
