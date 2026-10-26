"""Emails go out as HTML rendered from the Markdown body, with a clean plain-text part."""

from nexus_common import mail
from nexus_common.settings import Settings

BODY = """Here are two things worth a look:

1. **Adobe's design trends**
   https://www.adobe.com/trends
   Why it's interesting: organic design.
2. **Brand New** by [UnderConsideration](https://www.underconsideration.com/brandnew)

## Next
| a | b |
|---|---|
| 1 | 2 |

<script>alert(1)</script>
"""


def test_markdown_body_becomes_html_with_plain_fallback():
    msg = mail.message(Settings(smtp_from="nexus@test"), ["me@test"], "Weekly", BODY)
    assert msg.get_content_type() == "multipart/alternative"
    text = msg.get_body(("plain",)).get_content()
    page = msg.get_body(("html",)).get_content()

    assert "**" not in text and "## " not in text
    assert "Brand New by UnderConsideration (https://www.underconsideration.com/brandnew)" in text
    assert "1. Adobe's design trends\n   https://www.adobe.com/trends" in text

    assert "<strong" in page and "**" not in page
    assert '<a href="https://www.adobe.com/trends" style="color:#2563eb">' in page
    assert "<br" in page  # single line breaks are kept
    assert "<table" in page and "border:1px solid" in page
    # Raw HTML from a model or web page is shown as text, never run.
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_attachments_still_ride_along():
    msg = mail.message(Settings(smtp_from="nexus@test"), ["me@test"], "Report", "See **attached**.")
    msg.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="r.pdf")
    assert msg.get_content_type() == "multipart/mixed"
    assert [a.get_filename() for a in msg.iter_attachments()] == ["r.pdf"]
    assert "<strong" in msg.get_body(("html",)).get_content()
