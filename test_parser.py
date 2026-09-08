#!/usr/bin/env python3
"""
Offline test af parser og ICS-generator.

Bruger gemte HTML-uddrag der gengiver strukturen paa fck.dk, saa logikken kan
verificeres uden netadgang. Koeres af GitHub Actions foer selve opdateringen.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

import build_calendar as bc

LIST_HTML = """
<html><body>
  <a href="/kommende-kampe">Kommende kampe</a>
  <a href="/kamp/18-10-26/fc-kobenhavn-agf">Go to match</a>
  <a href="/kamp/18-10-26/fc-kobenhavn-agf">Go to match</a>
  <a href="https://www.fck.dk/kamp/22-10-26/fk-crvena-zvezda-fc-kobenhavn">Go to match</a>
  <a href="/kamp/22-11-26/fc-nordsjaelland-fc-kobenhavn">Go to match</a>
  <a href="/kampe?season=All">Kamparkiv</a>
</body></html>
"""

HOME_HTML = """
<html><head>
  <meta property="og:title" content="F.C. K\u00f8benhavn vs. AGF 2026-10-18  | 18/10 2026 18:00 | 3F Superliga">
  <meta name="geo.placename" content="Parken - connected by 3">
</head><body>
  <h1>F.C. K\u00f8benhavn vs. AGF 2026-10-18</h1>
  <div><p>Parken - connected by 3 | TV3+ / Viaplay</p></div>
  <a href="https://billet.fck.dk/">K\u00f8b billet</a>
</body></html>
"""

HOME_HTML_SPLIT_ELEMENTS = """
<html><head>
  <meta property="og:title" content="F.C. K\u00f8benhavn vs. OB 2026-10-26  | 26/10 2026 19:00 | 3F Superliga">
  <meta name="geo.placename" content="Parken - connected by 3">
</head><body>
  <div><span>Parken - connected by 3</span><span>|</span><span>TV3 SPORT</span></div>
</body></html>
"""

AWAY_TBC_HTML = """
<html><head>
  <meta property="og:title" content="FC Nordsj\u00e6lland vs. F.C. K\u00f8benhavn 2026-11-22  | 22/11 2026 00:00 | 3F Superliga">
  <meta name="geo.placename" content="Right to Dream Park">
</head><body>
  <div><p>Right to Dream Park</p></div>
</body></html>
"""

FRIENDLY_HOME_HTML = """
<html><head>
  <meta property="og:title" content="F.C. K\u00f8benhavn vs. Hamburger SV 2026-10-04  | 04/10 2026 15:30 | Tr\u00e6ningskamp">
  <meta name="geo.placename" content="Parken - connected by 3">
</head><body><div><p>Parken - connected by 3 | Ikke i tv</p></div></body></html>
"""

FALLBACK_TITLE_HTML = """
<html><head>
  <title>F.C. K\u00f8benhavn vs. S.C. Braga 2026-10-15  | 15/10 2026 21:00 | UEFA Conference League | F.C. K\u00f8benhavn</title>
  <meta name="geo.placename" content="Parken - connected by 3">
</head><body><div><p>Parken - connected by 3 | TV3 MAX</p></div></body></html>
"""


def soup(html):
    return BeautifulSoup(html, "html.parser")


def build(html, url="https://www.fck.dk/kamp/x/y"):
    """Genskaber parse_match_page uden netkald."""
    page = soup(html)
    raw = bc.meta_content(page, prop="og:title") or (page.title.get_text() if page.title else "")
    home, away, comp, start = bc.parse_title(raw)
    confirmed = (start.hour, start.minute) != bc.TBC_SOURCE_TIME
    if not confirmed:
        start = start.replace(hour=bc.TBC_PLACEHOLDER_TIME[0], minute=bc.TBC_PLACEHOLDER_TIME[1])
    venue = bc.meta_content(page, name="geo.placename")
    return bc.Match(
        url=url,
        home=home,
        away=away,
        competition=comp,
        start_local=start.replace(tzinfo=bc.LOCAL_TZ),
        venue=venue,
        tv=bc.extract_tv(page, venue),
        time_confirmed=confirmed,
    )


def test_list_links():
    urls = bc.collect_match_urls(soup(LIST_HTML))
    assert urls == [
        "https://www.fck.dk/kamp/18-10-26/fc-kobenhavn-agf",
        "https://www.fck.dk/kamp/22-10-26/fk-crvena-zvezda-fc-kobenhavn",
        "https://www.fck.dk/kamp/22-11-26/fc-nordsjaelland-fc-kobenhavn",
    ], urls
    print("ok: kamplinks, dubletter og absolutte adresser")


def test_home_match():
    m = build(HOME_HTML)
    assert m.home == "F.C. K\u00f8benhavn" and m.away == "AGF", (m.home, m.away)
    assert m.competition == "3F Superliga"
    assert m.start_local == datetime(2026, 10, 18, 18, 0, tzinfo=ZoneInfo("Europe/Copenhagen"))
    assert m.venue == "Parken - connected by 3"
    assert m.tv == "TV3+ / Viaplay", m.tv
    assert m.is_home and m.gets_ticket_alarm and m.time_confirmed
    print("ok: hjemmekamp, spillested, tv og billetpaamindelse")


def test_tv_split_across_elements():
    m = build(HOME_HTML_SPLIT_ELEMENTS)
    assert m.tv == "TV3 SPORT", m.tv
    print("ok: tv-kanal naar spillested og kanal ligger i hvert sit element")


def test_away_and_tbc():
    m = build(AWAY_TBC_HTML)
    assert not m.is_home and not m.gets_ticket_alarm
    assert not m.time_confirmed
    assert m.start_local.hour == 15 and m.start_local.minute == 0
    assert m.tv == ""
    assert "tidspunkt ikke bekræftet" in bc.build_summary(m)
    print("ok: udekamp og pladsholder for ubekraeftet tidspunkt")


def test_friendly_gets_no_alarm():
    m = build(FRIENDLY_HOME_HTML)
    assert m.is_home and not m.gets_ticket_alarm
    print("ok: hjemmekamp i traeningskamp faar ingen billetpaamindelse")


def test_title_fallback():
    m = build(FALLBACK_TITLE_HTML)
    assert m.competition == "UEFA Conference League", m.competition
    assert m.away == "S.C. Braga"
    print("ok: fald tilbage paa <title> med sitenavn i sidste led")


def test_ics_output():
    matches = [
        build(HOME_HTML),
        build(AWAY_TBC_HTML),
        build(FRIENDLY_HOME_HTML),
        build(FALLBACK_TITLE_HTML),
    ]
    ics = bc.build_ics(matches)

    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == 4
    # To hjemmekampe, men traeningskampen skal ikke have alarm.
    assert ics.count("BEGIN:VALARM") == 2, ics.count("BEGIN:VALARM")
    assert "TRIGGER:-PT36H" in ics
    # 18:00 dansk sommertid svarer til 16:00 UTC.
    assert "DTSTART:20261018T160000Z" in ics
    assert "DTEND:20261018T180000Z" in ics
    assert "STATUS:TENTATIVE" in ics
    assert "LOCATION:Parken - connected by 3" in ics
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, line
    print("ok: ICS-struktur, alarmer, UTC-tider og linjefoldning")


def test_uid_survives_date_change():
    original = build(HOME_HTML)
    moved = build(HOME_HTML)
    moved.start_local = moved.start_local.replace(day=19, hour=16)
    uid_a = bc.assign_uids([original])[id(original)]
    uid_b = bc.assign_uids([moved])[id(moved)]
    assert uid_a == uid_b
    print("ok: UID er uaendret naar en kamp flyttes")


if __name__ == "__main__":
    test_list_links()
    test_home_match()
    test_tv_split_across_elements()
    test_away_and_tbc()
    test_friendly_gets_no_alarm()
    test_title_fallback()
    test_ics_output()
    test_uid_survives_date_change()
    print("\nAlle test bestaaet.")
