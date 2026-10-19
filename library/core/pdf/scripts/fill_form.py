"""List or fill the fields of a PDF form (AcroForm) with pypdf.

python fill_form.py fields IN
python fill_form.py fill IN OUT values.json
"""

import argparse
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject


def fields(a):
    r = PdfReader(a.input)
    out = {}
    for name, f in (r.get_fields() or {}).items():
        out[name] = {
            "type": str(f.get("/FT", "")).lstrip("/"),
            "value": str(f.get("/V", "")),
            "options": [str(o) for o in f.get("/_States_", [])] or None,
        }
    print(json.dumps(out, indent=2) if out else "This PDF has no form fields.")


def fill(a):
    values = json.loads(Path(a.values).read_text("utf-8"))
    r = PdfReader(a.input)
    known = r.get_fields() or {}
    unknown = [k for k in values if k not in known]
    if unknown:
        raise SystemExit(f"Unknown fields: {unknown}. Run `fields` to list them.")
    w = PdfWriter(clone_from=r)
    text_values, checks = {}, {}
    for k, v in values.items():
        if str(known[k].get("/FT")) == "/Btn":
            states = [s for s in known[k].get("/_States_", []) if s != "/Off"]
            checks[k] = (states[0] if states else "/Yes") if v else "/Off"
        else:
            text_values[k] = str(v)
    for page in w.pages:
        if text_values:
            w.update_page_form_field_values(page, text_values, auto_regenerate=False)
        for annot in page.get("/Annots", []) or []:
            obj = annot.get_object()
            name = obj.get("/T") or (obj.get("/Parent") or {}).get("/T")
            if name in checks:
                state = NameObject(checks[name])
                obj[NameObject("/V")] = state
                obj[NameObject("/AS")] = state
    w.set_need_appearances_writer(True)
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    with open(a.output, "wb") as f:
        w.write(f)
    print(f"Filled {len(values)} fields into {a.output}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fields")
    p.add_argument("input")
    p.set_defaults(fn=fields)
    p = sub.add_parser("fill")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("values")
    p.set_defaults(fn=fill)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
