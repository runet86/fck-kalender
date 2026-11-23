#!/usr/bin/env python3
"""
Offline test af parser og ICS-generator.

Fixtures gengiver strukturen paa fck.dk efter omlaegningen i november 2026:
oversigten paa /kampe med absolutte links uden www, og kampsider hvor turnering
og spillested staar i og:description, kamptidspunktet i overskriften og
tv-kanalen i et etiketteret felt efter "Stadion".
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

import build_calendar as bc

# Menuen oeverst paa alle sider. Den indeholder et punkt der hedder praecis "TV",
# som tv-udtraekket ikke maa forveksle med kampens tv-felt.
NAV = """
  <nav><a href="https://fck.dk/">FCK.DK</a><a href="https://fck.dk/tv">TV</a>
  <a href="https://www.fckfanshop.dk">Fanshop</a><a href="https://fck.dk/billetter">Billetter</a></nav>
"""

LIST_HTML = f"""
<html><body>{NAV}
  <a href="https://fck.dk/kampe">Kommende kampe</a>
  <a href="https://fck.dk/kamp/26-11-26/inter-club-descaldes-fc-kobenhavn">Kampside</a>
  <a href="https://fck.dk/kamp/29-11-26/fc-kobenhavn-fc-midtjylland">Kampside</a>
  <a href="https://fck.dk/kamp/29-11-26/fc-kobenhavn-fc-midtjylland">Kampside igen</a>
  <a href="https://www.fck.dk/kamp/02-12-26/fc-kobenhavn-fc-midtjylland">Gammelt www-link</a>
  <a href="/kamp/21-02-27/fc-kobenhavn-viborg-ff">Relativt link</a>
  <a href="https://fck.dk/en/kamp/29-11-26/fc-kobenhavn-fc-midtjylland">English</a>
  <a href="https://billet.fck.dk/">Køb billet</a>
  <a href="https://fck.dk/kampe?scope=results">Resultater</a>
</body></html>
"""

HOME_CONFIRMED = f"""
<html><head>
  <meta property="og:title" content="F.C. København vs. FC Midtjylland 2026-11-29">
  <meta property="og:description" content="3F Superliga · Sæson 26/27 · Parken - connected by 3">
</head><body>{NAV}
  <h1>F.C. København mod FC Midtjylland, 29.11.2026 kl. 16.00</h1>
  <div><span>Runde</span><span>Runde 16</span></div>
  <div><span>Stadion</span><span>Parken - connected by 3</span></div>
  <div><span>TV</span><span>TV2 Sport X / TV2 Play</span></div>
  <div><span>Sejre</span><span>3825 hjemme / 13 ude</span></div>
</body></html>
"""

HOME_TBC = f"""
<html><head>
  <meta property="og:title" content="F.C. København vs. Viborg FF 2027-02-21">
  <meta property="og:description" content="3F Superliga · Sæson 26/27 · Parken - connected by 3">
</head><body>{NAV}
  <h1>F.C. København mod Viborg FF, 21.02.2027</h1>
  <div><span>Stadion</span><span>Parken - connected by 3</span></div>
  <div><span>Sejre</span><span>4426 hjemme / 18 ude</span></div>
</body></html>
"""

AWAY_EUROPE = f"""
<html><head>
  <meta property="og:title" content="Inter Club d'Escaldes vs. F.C. København 2026-11-26">
  <meta property="og:description" content="UEFA Conference League · Sæson 26/27 · Estadi de la FAF">
</head><body>{NAV}
  <h1>Inter Club d'Escaldes mod F.C. København, 26.11.2026 kl. 21.00</h1>
  <div><span>Stadion</span><span>Estadi de la FAF</span></div>
  <div><span>TV</span><span>TV3 MAX</span></div>
</body></html>
"""

HOME_FRIENDLY = f"""
<html><head>
  <meta property="og:title" content="F.C. København vs. Hamburger SV 2027-01-16">
  <meta property="og:description" content="Træningskamp · Sæson 26/27 · Parken - connected by 3">
</head><body>{NAV}
  <h1>F.C. København mod Hamburger SV, 16.01.2027 kl. 14.00</h1>
  <div><span>Stadion</span><span>Parken - connected by 3</span></div>
</body></html>
"""

NO_VENUE_FIELD = f"""
<html><head>
  <meta property="og:title" content="F.C. København vs. Randers FC 2027-03-07">
  <meta property="og:description" content="3F Superliga · Sæson 26/27 · Parken - connected by 3">
</head><body>{NAV}
  <h1>F.C. København mod Randers FC, 07.03.2027 kl. 18.00</h1>
</body></html>
"""


def soup(html):
    return BeautifulSoup(html, "html.parser")


def build(html, url="https://fck.dk/kamp/x/y"):
    """Genskaber parse_match_page uden netkald."""
    page = soup(html)
    raw_title = bc.meta_content(page, prop="og:title")
    home, away, match_date = bc.parse_teams_and_date(raw_title)
    competition, venue_meta = bc.parse_description(bc.meta_content(page, prop="og:description"))
    venue, tv = bc.extract_venue_and_tv(bc.page_lines(page))
    venue = venue or venue_meta
    kickoff = bc.parse_kickoff(page)
    hour, minute = kickoff if kickoff else bc.TBC_PLACEHOLDER_TIME
    return bc.Match(
        url=url,
        home=home,
        away=away,
        competition=competition,
        start_local=match_date.replace(hour=hour, minute=minute, tzinfo=bc.LOCAL_TZ),
        venue=venue,
        tv=tv,
        time_confirmed=kickoff is not None,
    )


def test_list_links():
    urls = bc.collect_match_urls(soup(LIST_HTML))
    assert urls == [
        "https://fck.dk/kamp/26-11-26/inter-club-descaldes-fc-kobenhavn",
        "https://fck.dk/kamp/29-11-26/fc-kobenhavn-fc-midtjylland",
        "https://fck.dk/kamp/02-12-26/fc-kobenhavn-fc-midtjylland",
        "https://fck.dk/kamp/21-02-27/fc-kobenhavn-viborg-ff",
    ], urls
    print("ok: kamplinks med og uden www, relative links, dubletter og engelsk udgave")


def test_home_confirmed():
    m = build(HOME_CONFIRMED)
    assert m.home == "F.C. København" and m.away == "FC Midtjylland", (m.home, m.away)
    assert m.competition == "3F Superliga", m.competition
    assert m.venue == "Parken - connected by 3", m.venue
    assert m.tv == "TV2 Sport X / TV2 Play", m.tv
    assert m.start_local == datetime(2026, 11, 29, 16, 0, tzinfo=ZoneInfo("Europe/Copenhagen"))
    assert m.is_home and m.gets_ticket_alarm and m.time_confirmed
    print("ok: hjemmekamp med turnering, spillested, tv-kanal og kampstart")


def test_nav_tv_is_not_mistaken_for_broadcaster():
    m = build(HOME_TBC)
    assert m.tv == "", m.tv
    print("ok: menupunktet TV forveksles ikke med kampens tv-felt")


def test_tbc_kickoff():
    m = build(HOME_TBC)
    assert not m.time_confirmed
    assert m.start_local.hour == 15 and m.start_local.minute == 0
    assert "tidspunkt ikke bekræftet" in bc.build_summary(m)
    assert m.gets_ticket_alarm
    print("ok: manglende kl. giver pladsholder kl. 15.00 og TENTATIVE")


def test_away_europe():
    m = build(AWAY_EUROPE)
    assert not m.is_home and not m.gets_ticket_alarm
    assert m.competition == "UEFA Conference League"
    assert m.venue == "Estadi de la FAF"
    assert m.tv == "TV3 MAX"
    print("ok: udekamp i Europa, ingen billetpåmindelse")


def test_friendly_home_gets_no_alarm():
    m = build(HOME_FRIENDLY)
    assert m.is_home and not m.gets_ticket_alarm
    print("ok: hjemmekamp i træningskamp får ingen billetpåmindelse")


def test_venue_falls_back_to_meta():
    m = build(NO_VENUE_FIELD)
    assert m.venue == "Parken - connected by 3", m.venue
    print("ok: spillested falder tilbage på og:description")


def test_description_parsing():
    assert bc.parse_description("3F Superliga · Sæson 26/27") == ("3F Superliga", "")
    assert bc.parse_description("") == ("", "")
    print("ok: og:description uden spillested giver tom spillestedsværdi")


def test_ics_output():
    matches = [
        build(HOME_CONFIRMED),
        build(HOME_TBC),
        build(AWAY_EUROPE),
        build(HOME_FRIENDLY),
    ]
    ics = bc.build_ics(matches)

    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == 4
    # To hjemmekampe med alarm; træningskampen og udekampen uden.
    assert ics.count("BEGIN:VALARM") == 2, ics.count("BEGIN:VALARM")
    assert "TRIGGER:-PT36H" in ics
    # 16.00 dansk normaltid svarer til 15:00 UTC.
    assert "DTSTART:20261129T150000Z" in ics
    assert "DTEND:20261129T170000Z" in ics
    assert "STATUS:TENTATIVE" in ics
    assert "LOCATION:Estadi de la FAF" in ics
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, line
    print("ok: ICS-struktur, alarmer, UTC-tider og linjefoldning")


def test_uid_survives_date_change():
    original = build(HOME_CONFIRMED)
    moved = build(HOME_CONFIRMED)
    moved.start_local = moved.start_local.replace(day=30, hour=19)
    assert bc.assign_uids([original])[id(original)] == bc.assign_uids([moved])[id(moved)]
    print("ok: UID er uændret når en kamp flyttes")


def test_two_meetings_same_teams_get_distinct_uids():
    first = build(HOME_CONFIRMED)
    second = build(HOME_CONFIRMED)
    second.start_local = second.start_local.replace(month=12, day=2)
    uids = bc.assign_uids([first, second])
    assert uids[id(first)] != uids[id(second)]
    print("ok: to møder mellem samme hold får hver sin UID")


if __name__ == "__main__":
    test_list_links()
    test_home_confirmed()
    test_nav_tv_is_not_mistaken_for_broadcaster()
    test_tbc_kickoff()
    test_away_europe()
    test_friendly_home_gets_no_alarm()
    test_venue_falls_back_to_meta()
    test_description_parsing()
    test_ics_output()
    test_uid_survives_date_change()
    test_two_meetings_same_teams_get_distinct_uids()
    print("\nAlle test bestået.")
