"""BibTeX helpers: make, check, de-duplicate and format references. See SKILL.md.

python bib_tools.py from-json refs.json out/references.bib
python bib_tools.py check references.bib
python bib_tools.py dedupe references.bib out/references.bib
python bib_tools.py format references.bib --style apa|ieee [--out refs.md]
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

REQUIRED = {
    "article": ["author", "title", "journal", "year"],
    "inproceedings": ["author", "title", "booktitle", "year"],
    "book": ["author", "title", "publisher", "year"],
    "misc": ["author", "title", "year"],
    "phdthesis": ["author", "title", "school", "year"],
    "techreport": ["author", "title", "institution", "year"],
}
CONFERENCES = re.compile(
    r"\b(conference|proceedings|workshop|symposium|neurips|nips|icml|iclr|acl|emnlp|naacl|cvpr|"
    r"iccv|eccv|aaai|ijcai|kdd|colm|coling|interspeech|icassp|sigir|www)\b",
    re.I,
)
STOP = {"a", "an", "the", "on", "of", "for", "in", "to", "and", "with", "towards", "toward"}


# --- Parsing ---------------------------------------------------------------------------------


def _value(text: str, i: int) -> tuple[str, int]:
    """A field value starting at text[i]: {braced}, "quoted" or a bare word/number."""
    if text[i] == "{":
        depth, j = 0, i
        while j < len(text):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            if depth == 0:
                return text[i + 1 : j], j + 1
            j += 1
        raise ValueError("unbalanced braces")
    if text[i] == '"':
        j = i + 1
        while j < len(text) and not (text[j] == '"' and text[j - 1] != "\\"):
            j += 1
        return text[i + 1 : j], j + 1
    m = re.match(r"[^,}\s]+", text[i:])
    return (m.group(0) if m else ""), i + (m.end() if m else 0)


def parse(text: str) -> list[dict]:
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        kind = m.group(1).lower()
        if kind in ("comment", "string", "preamble"):
            continue
        fields, i = {}, m.end()
        while i < len(text):
            while i < len(text) and text[i] in " \t\r\n,":
                i += 1
            if i >= len(text) or text[i] == "}":
                break
            f = re.match(r"([\w:-]+)\s*=\s*", text[i:])
            if not f:
                break
            i += f.end()
            value, i = _value(text, i)
            fields[f.group(1).lower()] = " ".join(value.split())
        entries.append({"type": kind, "key": m.group(2), "fields": fields})
    return entries


def dump(entries: list[dict]) -> str:
    out = []
    for e in entries:
        lines = [f"@{e['type']}{{{e['key']},"]
        for k, v in e["fields"].items():
            lines.append(f"  {k} = {{{v}}},")
        lines.append("}")
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


# --- Helpers ---------------------------------------------------------------------------------


def plain(s: str) -> str:
    s = re.sub(r"\\[a-zA-Z]+\s*", "", s).replace("{", "").replace("}", "")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", plain(s).lower().replace("'", "")).strip()


def authors(e: dict) -> list[str]:
    raw = e["fields"].get("author", "")
    return [a.strip() for a in re.split(r"\s+and\s+", raw) if a.strip()]


def split_name(name: str) -> tuple[str, str]:
    """(last, first) from 'Last, First' or 'First Middle Last'."""
    if "," in name:
        last, first = name.split(",", 1)
        return last.strip(), first.strip()
    parts = name.split()
    return (parts[-1], " ".join(parts[:-1])) if parts else ("", "")


def initials(first: str) -> str:
    return " ".join(f"{p[0]}." for p in re.split(r"[\s-]+", plain(first)) if p)


def ids(e: dict) -> set[str]:
    f = e["fields"]
    out = set()
    if f.get("doi"):
        out.add("doi:" + f["doi"].lower().removeprefix("https://doi.org/"))
    arxiv = f.get("eprint") or ""
    m = re.search(r"(\d{4}\.\d{4,5})", arxiv + " " + f.get("url", "") + " " + f.get("journal", ""))
    if m:
        out.add("arxiv:" + m.group(1))
    if f.get("title"):
        out.add("title:" + norm_title(f["title"]))
    return out


# --- Commands --------------------------------------------------------------------------------


def from_json(refs: list[dict]) -> list[dict]:
    entries, used = [], set()
    for r in refs:
        names = r.get("authors") or []
        if isinstance(names, str):
            names = [a.strip() for a in re.split(r",\s*|\s+and\s+", names) if a.strip()]
        venue = str(r.get("venue") or r.get("journal") or r.get("booktitle") or "")
        kind = r.get("type") or (
            "inproceedings"
            if venue and CONFERENCES.search(venue)
            else "article"
            if venue and "arxiv" not in venue.lower()
            else "misc"
        )
        f: dict[str, str] = {"title": str(r.get("title", "")), "author": " and ".join(names)}
        if r.get("year"):
            f["year"] = str(r["year"])
        if kind == "inproceedings" and venue:
            f["booktitle"] = venue
        elif kind == "article" and venue:
            f["journal"] = venue
        for k in ("volume", "number", "pages", "publisher", "doi", "url", "note"):
            if r.get(k):
                f[k] = str(r[k])
        if r.get("arxiv"):
            f["eprint"] = str(r["arxiv"])
            f["archiveprefix"] = "arXiv"
            f.setdefault("url", f"https://arxiv.org/abs/{r['arxiv']}")
        last = plain(split_name(names[0])[0]).lower() if names else "anon"
        word = next((w for w in norm_title(f["title"]).split() if w not in STOP), "untitled")
        base = re.sub(r"[^a-z0-9]", "", f"{last}{r.get('year', '')}{word}") or "ref"
        key, n = base, 1
        while key in used:
            n += 1
            key = f"{base}{chr(ord('a') + n - 2)}"
        used.add(key)
        entries.append({"type": kind, "key": key, "fields": f})
    return entries


def check(entries: list[dict]) -> int:
    problems = 0
    keys: dict[str, int] = {}
    for e in entries:
        keys[e["key"]] = keys.get(e["key"], 0) + 1
    for k, n in keys.items():
        if n > 1:
            print(f"duplicate key: {k} ({n} entries)")
            problems += 1
    for group in groups(entries):
        if len(group) > 1:
            print("same paper: " + ", ".join(e["key"] for e in group))
            problems += 1
    for e in entries:
        need = REQUIRED.get(e["type"], ["author", "title", "year"])
        missing = [f for f in need if not e["fields"].get(f)]
        if missing:
            print(f"{e['key']} ({e['type']}): missing {', '.join(missing)}")
            problems += 1
    print(f"{len(entries)} entries, {problems} problem(s)")
    return problems


def groups(entries: list[dict]) -> list[list[dict]]:
    """Entries that are the same paper (shared DOI, arXiv id or title), via union-find."""
    parent = list(range(len(entries)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[str, int] = {}
    for i, e in enumerate(entries):
        for ident in ids(e):
            if ident in seen:
                parent[find(i)] = find(seen[ident])
            else:
                seen[ident] = i
    out: dict[int, list[dict]] = {}
    for i, e in enumerate(entries):
        out.setdefault(find(i), []).append(e)
    return list(out.values())


def dedupe(entries: list[dict]) -> list[dict]:
    kept = []
    for group in groups(entries):
        # The most complete entry wins; published versions beat preprints.
        best = max(group, key=lambda e: (e["type"] != "misc", len(e["fields"])))
        for e in group:
            for k, v in e["fields"].items():
                best["fields"].setdefault(k, v)
            if e is not best:
                print(f"{e['key']} -> {best['key']}")
        kept.append(best)
    return kept


def fmt(e: dict, style: str) -> str:
    f = e["fields"]
    names = [split_name(plain(a)) for a in authors(e)]
    title = plain(f.get("title", "")).rstrip(".")
    venue = plain(f.get("booktitle") or f.get("journal") or "")
    if not venue and f.get("eprint"):
        venue = f"arXiv:{f['eprint']}"
    year = f.get("year", "n.d.")
    link = f.get("url") or (f"https://doi.org/{f['doi']}" if f.get("doi") else "")
    if style == "apa":
        people = [f"{last}, {initials(first)}".strip(", ") for last, first in names]
        if len(people) > 20:
            people = people[:19] + ["...", people[-1]]
        who = (
            ", ".join(people[:-1]) + (", & " if len(people) > 1 else "") + people[-1]
            if people
            else ""
        )
        s = f"{who} ({year}). {title}."
        if venue:
            s += f" *{venue}*."
    else:
        people = [f"{initials(first)} {last}".strip() for last, first in names]
        if len(people) > 6:
            people = [people[0] + " et al."]
        who = (
            ", ".join(people[:-1]) + (" and " if len(people) > 1 else "") + people[-1]
            if people
            else ""
        )
        s = f'{who}, "{title},"'
        if venue:
            s += f" *{venue}*,"
        s += f" {year}."
    if link:
        s += f" {link}"
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("from-json", help="list of reference objects -> .bib")
    j.add_argument("refs")
    j.add_argument("out")
    c = sub.add_parser("check", help="duplicates and missing fields")
    c.add_argument("bib")
    d = sub.add_parser("dedupe", help="merge duplicate papers")
    d.add_argument("bib")
    d.add_argument("out")
    f = sub.add_parser("format", help="reference list in Markdown")
    f.add_argument("bib")
    f.add_argument("--style", choices=["apa", "ieee"], default="apa")
    f.add_argument("--out")
    a = ap.parse_args()

    if a.cmd == "from-json":
        refs = json.loads(Path(a.refs).read_text("utf-8"))
        entries = from_json(refs)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(dump(entries), "utf-8")
        print("\n".join(f"{e['key']}: {e['fields']['title']}" for e in entries))
        return
    entries = parse(Path(a.bib).read_text("utf-8"))
    if a.cmd == "check":
        sys.exit(1 if check(entries) else 0)
    if a.cmd == "dedupe":
        kept = dedupe(entries)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(dump(kept), "utf-8")
        print(f"{len(entries)} -> {len(kept)} entries")
        return
    if a.style == "apa":
        entries.sort(
            key=lambda e: (
                plain(e["fields"].get("author", "")).lower(),
                e["fields"].get("year", ""),
            )
        )
        lines = [fmt(e, "apa") for e in entries]
    else:
        lines = [f"[{i}] {fmt(e, 'ieee')}" for i, e in enumerate(entries, 1)]
    text = "\n\n".join(lines) + "\n"
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, "utf-8")
    print(text)


if __name__ == "__main__":
    main()
