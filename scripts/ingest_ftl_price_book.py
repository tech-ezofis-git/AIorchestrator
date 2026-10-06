"""
Ingest FTL's Contractor Price Book (.docx) into the app (Estimator and Qualifier share this script).

Usage:
    python scripts/ingest_ftl_price_book.py "/path/to/FTL_Wittur_Products_2026_Price_List.docx"
    python scripts/ingest_ftl_price_book.py <docx> --dry-run     # parse + show what would change, write nothing
    python scripts/ingest_ftl_price_book.py <docx> --no-embed    # write the price overlay only (no OpenAI call)

What it does, in order (and why that order):
  1. Parses the catalogue tables and VALIDATES them (aborts, writing nothing, on anything odd).
  2. Prints a change report vs. the current Stage 2 Product Master: price changes, renamed codes,
     new items, and items the price book doesn't cover (those keep their existing price).
  3. Re-indexes the searchable pricelist (data/pricelist_index.json) — this is the only step that
     needs OPENAI_API_KEY (it embeds ~13 short pages). Skipped with --no-embed or --dry-run.
  4. Writes data/rulebook/price_book_overlay.json — the deterministic price/code table that
     rules_engine.py layers over the Product Master. Written after the index so the two always
     describe the same revision.

Re-run it whenever FTL sends a new price book, then run the test suite under tests/.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.ftl.qualifier import price_book  # noqa: E402


def _raw_master():
    with open(
        os.path.join(ROOT, "app", "ftl", "quote_estimator", "data", "rulebook", "product_master.json"),
        encoding="utf-8",
    ) as f:
        return {p["ftl_product_code"]: p for p in json.load(f) if p.get("ftl_product_code")}


def change_report(book) -> str:
    master = _raw_master()
    covered = set()
    price_changes, renames, new_rows = [], [], []
    for r in book["rows"]:
        code, aliases = r["code"], r["aliases"]
        old_code = code if code in master else next((a for a in aliases if a in master), None)
        if old_code is None:
            new_rows.append(r)
            continue
        covered.add(old_code)
        if old_code != code:
            renames.append((old_code, code))
        old_price = master[old_code].get("price_cad")
        if old_price is not None and abs(float(old_price) - r["price_cad"]) > 0.005:
            price_changes.append((code, old_price, r["price_cad"]))
    not_covered = [c for c in master if c not in covered]

    out = [f"Price book {book['revision']}: {len(book['rows'])} catalogue rows"]
    out.append(f"\nPRICE CHANGES ({len(price_changes)}):")
    out += [f"  {c}: ${o:,.2f} -> ${n:,.2f}" for c, o, n in price_changes] or ["  none"]
    out.append(f"\nRENAMED CODES ({len(renames)}) — old code stays accepted, rewritten to the new one:")
    shown = renames[:6]
    out += [f"  {o} -> {n}" for o, n in shown]
    if len(renames) > len(shown):
        out.append(f"  ... and {len(renames) - len(shown)} more of the same two patterns (_LH/_RH -> _L/_R, _DP_ -> _PANEL_ADAPTOR_)")
    out.append(f"\nNEW ITEMS not in the Product Master ({len(new_rows)}):")
    out += [f"  {r['code']}  ${r['price_cad']:,.2f}" for r in new_rows] or ["  none"]
    out.append(f"\nNOT IN THE PRICE BOOK ({len(not_covered)}) — keep their existing price:")
    fams = {}
    for c in not_covered:
        fams.setdefault(master[c].get("family"), []).append(c)
    out += [f"  {f}: {len(v)} items" for f, v in sorted(fams.items(), key=lambda kv: str(kv[0]))]
    return "\n".join(out)


def main(argv) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    if len(args) != 1:
        print(__doc__)
        return 2
    path = args[0]
    try:
        book = price_book.parse_price_book(path)
    except price_book.PriceBookError as e:
        print(f"ABORTED — nothing was written. {e}")
        return 1
    print(change_report(book))

    if "--dry-run" in flags:
        print("\n--dry-run: nothing written.")
        return 0

    if "--no-embed" in flags:
        price_book.write_overlay(book)
        print(f"\nWrote {price_book.OVERLAY_PATH}. Index NOT rebuilt (--no-embed): the searchable index "
              "still holds the previous pricelist text until you run this without --no-embed.")
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        price_book.write_overlay(book)
        print(f"\nWrote {price_book.OVERLAY_PATH} (deterministic prices/codes are now live).\n"
              "OPENAI_API_KEY is not set, so the searchable index was NOT rebuilt. Re-run with the key "
              "exported to finish:\n    export OPENAI_API_KEY=sk-...   # your own key, not stored anywhere\n"
              f"    python scripts/ingest_ftl_price_book.py \"{path}\"")
        return 3

    from app.ftl.qualifier import pricelist_store as qualifier_pricelist  # noqa: E402
    from app.ftl.quote_estimator import pricelist_store as estimator_pricelist  # noqa: E402

    qualifier_result = qualifier_pricelist.reindex_from_price_book(path)
    estimator_result = estimator_pricelist.reindex_from_price_book(path, write_overlay=False)
    print(
        f"\nQualifier index: {qualifier_result['pages_indexed']} pages, {qualifier_result['total_chunks']} chunks. "
        f"Estimator index: {estimator_result['pages_indexed']} pages, {estimator_result['total_chunks']} chunks. "
        f"Overlay written to {price_book.OVERLAY_PATH}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
