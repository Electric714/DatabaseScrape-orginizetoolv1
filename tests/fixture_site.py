"""Deterministic miniature directory. No production website is contacted."""
import hashlib
import json
import httpx


class DirectorySite:
    def __init__(self):
        self.version = 1
        self.requests = []

    def profile(self, identifier, name, phone):
        record = {"@type": "Person", "identifier": identifier, "name": name,
                  "telephone": phone, "dateModified": "2026-09-05",
                  "address": {"streetAddress": "12 Oak Rd", "addressLocality": "Madison",
                              "addressRegion": "WI", "postalCode": "53703"}}
        return '<script type="application/ld+json">' + json.dumps(record) + '</script>'

    def __call__(self, request):
        path = request.url.path
        self.requests.append((path, dict(request.headers)))
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\nSitemap: https://fixture.test/sitemap.xml")
        if path == "/sitemap.xml":
            return httpx.Response(200, text='<sitemapindex><sitemap><loc>/map.xml</loc></sitemap></sitemapindex>',
                                  headers={"content-type": "application/xml"})
        if path == "/map.xml":
            return httpx.Response(200, text='<urlset><url><loc>/jane</loc></url><url><loc>/jane</loc></url></urlset>',
                                  headers={"content-type": "application/xml"})
        pages = {
            "/": '<a href="/page/2">Next</a><a href="/jane">Jane</a>',
            "/page/2": '<a href="/john">John</a>' + ('<a href="/new">New</a>' if self.version > 1 else ''),
            "/jane": self.profile("P1", "Jane Doe", "608-555-1111" if self.version == 1 else "608-555-2222"),
            "/john": self.profile("P2", "John Smith", "608-555-3333"),
            "/new": self.profile("P3", "New Person", "608-555-4444"),
        }
        if path not in pages:
            return httpx.Response(404)
        body = pages[path]
        etag = '"' + hashlib.sha256(body.encode()).hexdigest() + '"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"etag": etag})
        return httpx.Response(200, text=body, headers={"etag": etag, "content-type": "text/html; charset=utf-8"})
