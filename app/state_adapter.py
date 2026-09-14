"""Minnesota OSP's public, finite suspension/debarment list.

This is procurement debarment, not a substitute for Minnesota DLI eligibility.
Historical entries are retained as evidence and never treated as active.
"""
import re
from datetime import date, datetime, timezone
from bs4 import BeautifulSoup
from .identity import location_corroborates
from .osha_adapter import company_core, contractor_aliases

MN_URL = "https://mn.gov/admin/osp/government/suspended-debarred/"


def parse_minnesota(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    count = re.search(r"Results\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)", soup.get_text(" ", strip=True))
    blocks = soup.select(".search-results .results")
    if not count or int(count[1]) != 1 or int(count[2]) != int(count[3]) or len(blocks) != int(count[3]):
        raise ValueError("Minnesota list incomplete or layout changed; no negative conclusion")
    records = []
    for block in blocks:
        heading = block.select_one(".result-link a")
        if not heading:
            raise ValueError("Minnesota vendor name missing")
        fields, address_lines = {}, []
        for tr in block.select("table tr"):
            cells = tr.find_all("td", recursive=False)
            if len(cells) == 2:
                fields[cells[0].get_text(" ", strip=True).rstrip(":")] = cells[1].get_text(" ", strip=True)
            elif len(cells) == 1:
                address_lines.append(cells[0].get_text(" ", strip=True))
        location = next((re.fullmatch(r"(.*?),\s*([A-Z]{2})(?:\s+(\d{5}(?:-\d{4})?))?", line) for line in address_lines if re.fullmatch(r"(.*?),\s*([A-Z]{2})(?:\s+(\d{5}(?:-\d{4})?))?", line)), None)
        records.append({
            "name": heading.get_text(" ", strip=True),
            "address": address_lines[0] if address_lines else "",
            "city": location[1] if location else "", "state": location[2] if location else "",
            "zip": (location[3] or "") if location else "", "dates_and_cause": fields,
            "source_url": MN_URL + "#" + heading.get("id", ""),
        })
    return records


def active_on(record: dict, today: date) -> bool | None:
    uncertain = False
    for kind in ("Suspension", "Debarment"):
        fields = record["dates_and_cause"]
        start, end = fields.get(kind + " Date"), fields.get(kind + " End Date")
        if not start and not end:
            continue
        try:
            first = datetime.strptime(start, "%m/%d/%Y").date()
            last = datetime.strptime(end, "%m/%d/%Y").date()
            if last < first:
                uncertain = True
            elif first <= today <= last:
                return True
        except (ValueError, TypeError):
            uncertain = True
    return None if uncertain else False


class MinnesotaDebarmentAdapter:
    query_mode = always_parse = fail_fast_access_errors = True
    canonical_start_url = MN_URL
    master_fields = ("state_federal_debarment",)

    def seed_urls(self, master_rows):
        self.contractors = master_rows
        self.entries = []
        self.parsed = False
        # One small official list, filtered locally to the selected master rows.
        return [MN_URL]

    def allowed_url(self, url):
        return url == MN_URL

    def links(self, html, url):
        self.entries = parse_minnesota(html)
        self.parsed = True
        return []

    def extract(self, html, url):
        return []

    def finalize_records(self, complete):
        now = datetime.now(timezone.utc)
        results = []
        for row in self.contractors:
            aliases = contractor_aliases(row)
            cores = {company_core(a) for a in aliases}
            candidates = [dict(e) for e in self.entries if company_core(e["name"]) in cores]
            for item in candidates:
                item["match"] = "Confirmed" if location_corroborates(item, row) else "Likely / needs review"
                item["active"] = active_on(item, now.date())
            positive = any(e["match"] == "Confirmed" and e["active"] is True for e in candidates)
            done = complete and self.parsed
            results.append({
                "external_id": "mn:bidder:" + str(row.get("_master_id") or row.get("id")),
                "bidder_id": row.get("id", ""), "company": row["contractor_name"], "source_url": MN_URL,
                "state_federal_debarment": "Y" if positive else "",
                "extra": {"master_id": row.get("_master_id"), "source_system": "Minnesota OSP",
                    "api_fields_written_to_master": list(self.master_fields), "retrieved_at": now.isoformat(),
                    "complete_aggregate": done, "search_terms": aliases, "candidates": candidates,
                    "list_records_checked": len(self.entries), "jurisdiction": "MN",
                    "narrative": "Active matched Minnesota procurement debarment" if positive else
                        ("No confirmed active match in this Minnesota list; combined field unchanged" if done else "UNKNOWN / INCOMPLETE"),
                    "coverage": "Minnesota procurement list only; not federal or Minnesota DLI coverage"},
            })
        return results
