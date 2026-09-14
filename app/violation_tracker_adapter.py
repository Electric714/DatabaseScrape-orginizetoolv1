"""Targeted public HTML collector. Live validation is blocked by HTTP 403.

No subscriber endpoints or bulk downloads. Unknown layouts fail closed.
"""
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs
from bs4 import BeautifulSoup
from .osha_adapter import company_core, contractor_aliases
from .identity import location_corroborates

VT_URL = "https://violationtracker.goodjobsfirst.org/"
VT_FIELDS = ("environmental_violations", "prevailing_wage_violations", "misc_violations")


def offense_field(offense):
    # A wage-and-hour violation alone does not establish prevailing-wage abuse.
    value = offense.casefold().strip()
    if value in {"air pollution violation", "water pollution violation", "environmental violation", "hazardous waste violation"}:
        return "environmental_violations"
    if "prevailing wage" in value or "davis-bacon" in value:
        return "prevailing_wage_violations"
    if value in {"False Claims Act and related".casefold(), "government contracting violation", "bribery", "price-fixing or anti-competitive practices"}:
        return "misc_violations"
    return None


class ViolationTrackerAdapter:
    query_mode = always_parse = fail_fast_access_errors = True
    canonical_start_url = VT_URL
    master_fields = VT_FIELDS

    def seed_urls(self, master_rows):
        self.contractors = master_rows
        self.contexts = defaultdict(list)
        self.candidates = defaultdict(list)
        self.details = defaultdict(list)
        self.expected = {}
        self.seen_records = defaultdict(set)
        urls = []
        for i, row in enumerate(master_rows):
            for alias in contractor_aliases(row):
                term = company_core(alias)
                if not term:
                    continue
                self.contexts[term].append(i)
                urls.append(VT_URL + "?" + urlencode({"company": term, "company_op": "starts"}))
        return list(dict.fromkeys(urls))

    def allowed_url(self, url):
        p = urlsplit(url)
        return p.hostname == urlsplit(VT_URL).hostname and (p.path == "/" and bool(parse_qs(p.query).get("company")) or p.path.startswith("/violation-tracker/"))

    def links(self, html, url):
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        if urlsplit(url).path == "/":
            term = (parse_qs(urlsplit(url).query).get("company") or [""])[0]
            count = re.search(r"([\d,]+)\s+Violation Tracker results? found", text, re.I)
            if not count:
                raise ValueError("Violation Tracker search structure unrecognized; UNKNOWN")
            self.expected[term] = int(count[1].replace(",", ""))
            links = []
            for tr in soup.select("table tr"):
                a = tr.select_one('a[href*="/violation-tracker/"]')
                if not a:
                    continue
                detail_url = urljoin(url, a["href"])
                self.seen_records[term].add(detail_url)
                for i in self.contexts[term]:
                    if company_core(a.get_text(" ", strip=True)) in {company_core(x) for x in contractor_aliases(self.contractors[i])}:
                        self.details[detail_url].append(i)
                        links.append(detail_url)
            for a in soup.select('a[href]'):
                target = urljoin(url, a["href"])
                q = parse_qs(urlsplit(target).query)
                if "next" in a.get_text(" ", strip=True).casefold() and q.get("company") == [term] and self.allowed_url(target):
                    links.append(target)
            return list(dict.fromkeys(links))
        fields = {}
        for tr in soup.select("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) == 2:
                fields[cells[0].get_text(" ", strip=True).rstrip(":").casefold()] = cells[1].get_text(" ", strip=True)
        if not fields.get("company") or not fields.get("primary offense"):
            raise ValueError("Violation Tracker detail layout unverified; UNKNOWN")
        candidate = {"name": fields["company"], "city": fields.get("city", ""), "state": fields.get("state", ""),
            "zip": fields.get("zip", ""), "address": fields.get("address", ""), "source_url": url,
            "offense": fields["primary offense"], "agency": fields.get("agency", ""),
            "penalty": fields.get("penalty amount", ""), "year": fields.get("year", ""),
            "parent": fields.get("current parent company", "")}
        for i in self.details[url]:
            exact = company_core(candidate["name"]) in {company_core(a) for a in contractor_aliases(self.contractors[i])}
            self.candidates[i].append({**candidate, "match": "Confirmed" if exact and location_corroborates(candidate, self.contractors[i]) else "Likely / needs review"})
        return []

    def extract(self, html, url):
        return []

    def finalize_records(self, complete):
        results = []
        for i, row in enumerate(self.contractors):
            terms = [company_core(a) for a in contractor_aliases(row) if company_core(a)]
            done = complete and all(t in self.expected and len(self.seen_records[t]) == self.expected[t] for t in terms)
            fields = {f: "" for f in VT_FIELDS}
            for candidate in self.candidates[i]:
                field = offense_field(candidate["offense"])
                if candidate["match"] == "Confirmed" and field:
                    fields[field] = "Y"
            results.append({"external_id": "vt:bidder:" + str(row.get("_master_id") or row.get("id")),
                "bidder_id": row.get("id", ""), "company": row["contractor_name"],
                "source_url": VT_URL + "?" + urlencode({"company": terms[0], "company_op": "starts"}), **fields,
                "extra": {"master_id": row.get("_master_id"), "source_system": "Violation Tracker public HTML",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(), "complete_aggregate": done,
                    "search_terms": terms, "candidates": self.candidates[i], "api_fields_written_to_master": list(VT_FIELDS),
                    "narrative": "Public HTML adapter; live validation pending. Absence never proposes N.",
                    "coverage": "Selected enforcement records; subscriber-only fields are not accessed"}})
        return results
