"""Web search and page fetching for Jarvis and its agents.

Search uses Tavily or Brave when their key is set, otherwise DuckDuckGo's HTML endpoint (free,
no key, but rate limited). Fetching only follows public http(s) addresses: hosts that resolve
to private, loopback or link-local addresses are refused, so a page can't steer an agent into
the Compose network or the Mac's own services.
"""

import asyncio
import html
import io
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx

from jarvis_common.settings import Settings

USER_AGENT = "Mozilla/5.0 (compatible; JarvisAgent/0.3; +https://jarvis.servhub.cc)"
MAX_BYTES = 4_000_000
MAX_REDIRECTS = 5


class WebError(RuntimeError):
    """Safe to show to the agent."""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class _Text(HTMLParser):
    """HTML to readable text: drops scripts, styles and navigation, keeps block breaks."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"}
    BLOCK = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "section",
        "article",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "ul",
        "ol",
        "pre",
        "blockquote",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3") and not self.skip:
            self.parts.append("#" * int(tag[1]) + " ")
        if tag == "li" and not self.skip:
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


def html_to_text(page: str) -> tuple[str, str]:
    p = _Text()
    p.feed(page)
    p.close()
    return p.title.strip(), p.text()


async def check_public(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebError("Only public http(s) URLs can be fetched.")
    host = parts.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or 443)
    except socket.gaierror as exc:
        raise WebError(f"Can't resolve {host}.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise WebError(f"{host} is not a public address.")


class WebClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=10),
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def provider(self) -> str:
        choice = self.settings.web_search_provider
        if choice != "auto":
            return choice
        if self.settings.tavily_api_key:
            return "tavily"
        if self.settings.brave_api_key:
            return "brave"
        return "duckduckgo"

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        limit = max(1, min(limit, 10))
        try:
            match self.provider():
                case "tavily":
                    return await self._tavily(query, limit)
                case "brave":
                    return await self._brave(query, limit)
                case _:
                    return await self._duckduckgo(query, limit)
        except httpx.HTTPError as exc:
            raise WebError(f"Web search failed: {exc.__class__.__name__}") from exc

    async def _tavily(self, query: str, limit: int) -> list[SearchResult]:
        resp = await self._client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self.settings.tavily_api_key}"},
            json={"query": query, "max_results": limit, "search_depth": "basic"},
        )
        if resp.status_code >= 400:
            raise WebError(f"Tavily search failed ({resp.status_code}).")
        return [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", "")[:500])
            for r in resp.json().get("results", [])
        ]

    async def _brave(self, query: str, limit: int) -> list[SearchResult]:
        resp = await self._client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": limit},
            headers={
                "X-Subscription-Token": self.settings.brave_api_key,
                "Accept": "application/json",
            },
        )
        if resp.status_code >= 400:
            raise WebError(f"Brave search failed ({resp.status_code}).")
        results = resp.json().get("web", {}).get("results", [])
        return [
            SearchResult(
                r.get("title", ""), r.get("url", ""), html_to_text(r.get("description", ""))[1]
            )
            for r in results
        ]

    async def _duckduckgo(self, query: str, limit: int) -> list[SearchResult]:
        resp = await self._client.post(
            "https://html.duckduckgo.com/html/", data={"q": query, "kl": "wt-wt"}
        )
        if resp.status_code >= 400:
            raise WebError(
                f"DuckDuckGo search failed ({resp.status_code}); add a Tavily or Brave key "
                "for reliable search."
            )
        return parse_duckduckgo(resp.text)[:limit]

    async def fetch(self, url: str, max_chars: int = 15000) -> tuple[str, str, str]:
        """(final url, title, text). PDFs are converted to text too."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await check_public(current)
            try:
                async with self._client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        current = urljoin(current, resp.headers.get("location", ""))
                        continue
                    if resp.status_code >= 400:
                        raise WebError(f"{current} returned HTTP {resp.status_code}.")
                    data = bytearray()
                    async for chunk in resp.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            break
                    ctype = resp.headers.get("content-type", "").lower()
                    encoding = resp.encoding or "utf-8"
            except httpx.HTTPError as exc:
                raise WebError(f"Couldn't fetch {current}: {exc.__class__.__name__}") from exc
            title, text = _to_text(bytes(data), ctype, encoding, current)
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n[Cut at {max_chars} of {len(text)} characters.]"
            return current, title, text
        raise WebError("Too many redirects.")


def _to_text(data: bytes, ctype: str, encoding: str, url: str) -> tuple[str, str]:
    if "pdf" in ctype or data[:5] == b"%PDF-":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [p.extract_text() or "" for p in reader.pages[:60]]
        except Exception as exc:
            raise WebError(f"{url} is a PDF that couldn't be read.") from exc
        title = str((reader.metadata or {}).get("/Title") or "")
        return title, "\n\n".join(pages).strip()
    page = data.decode(encoding, errors="replace")
    if "html" in ctype or page.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        return html_to_text(page)
    if ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
        return "", page
    raise WebError(f"{url} is {ctype or 'binary data'}, not a page that can be read as text.")


_DDG_LINK = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>', re.S
)
_DDG_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(?P<snippet>.*?)</(?:a|div|td)>', re.S)


def parse_duckduckgo(page: str) -> list[SearchResult]:
    links = list(_DDG_LINK.finditer(page))
    out = []
    for i, m in enumerate(links):
        href = html.unescape(m.group("href"))
        if href.startswith("//"):
            href = "https:" + href
        q = parse_qs(urlsplit(href).query)
        if "uddg" in q:
            href = unquote(q["uddg"][0])
        if "duckduckgo.com/y.js" in href or not href.startswith("http"):
            continue  # ads and internal links
        end = links[i + 1].start() if i + 1 < len(links) else len(page)
        sm = _DDG_SNIPPET.search(page, m.end(), end)
        title = html_to_text(m.group("title"))[1]
        snippet = html_to_text(sm.group("snippet"))[1] if sm else ""
        out.append(SearchResult(title, href, snippet))
    return out
