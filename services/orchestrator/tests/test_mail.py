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


def test_sender_is_nexus_and_agent_mail_replies_to_the_person():
    s = Settings(smtp_from="nexus@servhub.cc")
    assert mail.sender(s) == "Nexus <nexus@servhub.cc>"
    assert (
        mail.sender(Settings(smtp_from="ServHub <nexus@servhub.cc>"))
        == "ServHub <nexus@servhub.cc>"
    )
    # Resend signs in as "resend", which isn't an address; From must not become that name.
    assert mail.sender(Settings(smtp_username="resend")) == "resend"

    msg = mail.message(s, ["a@test"], "Hi", "x")
    assert msg["From"] == "Nexus <nexus@servhub.cc>" and msg["Reply-To"] is None

    msg = mail.message(
        s, ["a@test"], "Hi", "x", reply_to="shivam@test", on_behalf_of="Shivam\r\nBcc: x@evil"
    )
    assert msg["From"] == '"Shivam Bcc: x@evil via Nexus" <nexus@servhub.cc>'
    assert msg["Reply-To"] == "shivam@test"

    s2 = Settings(smtp_from="nexus@servhub.cc", smtp_reply_to="me@test")
    assert mail.message(s2, ["a@test"], "Hi", "x")["Reply-To"] == "me@test"
    assert mail.message(s2, ["a@test"], "Hi", "x", reply_to="b@test")["Reply-To"] == "b@test"


def test_port_465_uses_tls_from_the_start(monkeypatch):
    used = []

    class Fake:
        def __init__(self, *a, **k):
            used.append(type(self).__name__)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            used.append("starttls")

        def login(self, *a):
            pass

        def send_message(self, msg):
            pass

    class SMTP(Fake): ...

    class SMTP_SSL(Fake): ...

    monkeypatch.setattr(mail.smtplib, "SMTP", SMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", SMTP_SSL)
    msg = mail.message(Settings(smtp_from="n@test"), ["a@test"], "s", "b")
    mail._send(Settings(smtp_host="h", smtp_port=465, smtp_from="n@test"), msg)
    mail._send(Settings(smtp_host="h", smtp_port=587, smtp_from="n@test"), msg)
    assert used == ["SMTP_SSL", "SMTP", "starttls"]
