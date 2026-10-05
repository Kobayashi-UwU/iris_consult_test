"""Fixed communication templates. Regret emails deliberately do not use AI:
every unsuccessful candidate gets the same respectful, consistent message."""

REGRET_SUBJECT = "Your application to the Thara Energy Graduate Engineer Programme 2027"

REGRET_BODY = """Dear {first_name},

Thank you for applying to the Thara Energy Graduate Engineer Programme 2027 and for the time you put into your application.

We received a very large number of strong applications this year. After careful review against the programme's published criteria, we will not be taking your application forward to interview on this occasion.

This decision is about the fit with this year's intake, not your potential as an engineer. You are welcome to apply for future roles with us, and if you would like brief feedback on your application, simply reply to this email.

We wish you every success.

Talent Acquisition Team
Thara Energy"""


def regret_email(first_name: str) -> dict:
    return {"subject": REGRET_SUBJECT, "body": REGRET_BODY.format(first_name=first_name)}


def fill_placeholders(text: str, first_name: str, slot: str = "") -> str:
    out = text.replace("{{first_name}}", first_name)
    if slot:
        out = out.replace("{{interview_slot}}", slot)
    return out
