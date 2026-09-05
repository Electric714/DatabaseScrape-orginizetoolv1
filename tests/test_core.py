from app.crawler import canonicalize_url
from app.extractor import extract_records
from app.normalizer import entity_key, normalize_date, normalize_phone, record_hash


def test_url_canonicalization():
    assert canonicalize_url("HTTPS://Example.com//people?id=2&utm_source=x#top") == "https://example.com/people?id=2"


def test_normalizers():
    assert normalize_phone("+1 (608) 555-1234") == "608-555-1234"
    assert normalize_date("September 5, 2026") == "2026-09-05"


def test_entity_key_survives_phone_change():
    a = {"name": "Jane Doe", "company": "Acme", "phone": "608-555-1111", "source_url": "https://x/a"}
    b = {"name": "Jane Doe", "company": "Acme", "phone": "608-555-2222", "source_url": "https://x/a"}
    assert entity_key(a) == entity_key(b)
    assert record_hash(a) != record_hash(b)


def test_jsonld_extraction():
    html = """<html><head><script type="application/ld+json">{
      "@context":"https://schema.org","@type":"Person","name":"Jane Doe","telephone":"(608) 555-1111",
      "address":{"@type":"PostalAddress","streetAddress":"1 Main St","addressLocality":"Madison","addressRegion":"WI","postalCode":"53703"}
    }</script></head><body></body></html>"""
    records = extract_records(html, "https://example.com/jane")
    assert records
    assert records[0]["name"] == "Jane Doe"
    assert "1 Main St" in records[0]["address"]


def test_table_extraction():
    html = """<table><tr><th>Name</th><th>Company</th><th>Phone</th><th>Address</th></tr>
    <tr><td>John Smith</td><td>Smith LLC</td><td>608-555-1234</td><td>12 Oak Rd, Madison WI</td></tr></table>"""
    records = extract_records(html, "https://example.com/directory")
    assert any(r.get("name") == "John Smith" and r.get("company") == "Smith LLC" for r in records)
