#!/usr/bin/env python3
"""
Bygger en ICS-kalender med F.C. Koebenhavns kommende kampe.

Kilde: https://fck.dk/kampe og de enkelte kampsider paa fck.dk.
Output: public/fck.ics (plus public/index.html).

Koeres ugentligt af GitHub Actions. Se README.md.

Version 2 (23.11.2026): tilpasset fck.dk's omlagte site. Oversigten ligger nu
paa /kampe, links er uden www, og kampsiden har hverken geo.placename eller
tidspunkt i og:title. Turnering og spillested laeses nu af og:description,
kamptidspunktet af overskriften, og tv-kanalen af det etiketterede TV-felt.
"""

from __future__ import annotations

import hashlib
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# KONFIGURATION
# ---------------------------------------------------------------------------

BASE_URL = "https://fck.dk"
LIST_URL = f"{BASE_URL}/kampe"
ALLOWED_HOSTS = {"fck.dk", "www.fck.dk"}

CALENDAR_NAME = "FCK Kampe"
CALENDAR_DESC = "F.C. Københavns kommende kampe, opdateret ugentligt fra fck.dk"

LOCAL_TZ = ZoneInfo("Europe/Copenhagen")

# Kampens varighed i kalenderen.
MATCH_DURATION = timedelta(hours=2)

# Kampe uden fastlagt kampstart vises paa fck.dk som "Tidspunkt følger", og
# overskriften paa kampsiden mangler da "kl. HH.MM". De laegges paa denne
# pladsholdertid og markeres som ikke bekraeftet.
TBC_PLACEHOLDER_TIME = (15, 0)

# Paamindelse om at frigive billet: 36 timer (1,5 doegn) foer kampstart.
TICKET_ALARM_LEAD = timedelta(hours=36)

# Turneringer der IKKE udloeser billetpaamindelse (traeningskampe).
NO_TICKET_ALARM_KEYWORDS = ("traeningskamp", "træningskamp", "venskabskamp")

# Saet til True hvis din kalenderklient ignorerer VALARM i abonnerede feeds.
# Der oprettes da en separat 36-timers begivenhed "Frigiv billet" pr. hjemmekamp
# i stedet for en alarm inde i selve kampbegivenheden.
SEPARATE_TICKET_EVENTS = False

# Navnevarianter for FCK, brugt til at afgoere hjemme- eller udebane.
FCK_NAMES = {"f.c. kobenhavn", "fc kobenhavn", "f.c. koebenhavn", "fck"}

USER_AGENT = "fck-kalender/2.0 (personal calendar feed)"
REQUEST_PAUSE_SECONDS = 1.0
REQUEST_TIMEOUT = 30

OUTPUT_DIR = Path("public")
ICS_PATH = OUTPUT_DIR / "fck.ics"

MATCH_PATH_RE = re.compile(r"^/kamp/\d{2}-\d{2}-\d{2}/[a-z0-9\-]+$")
ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s*$")
KICKOFF_RE = re.compile(r"kl\.\s*(\d{1,2})[.:](\d{2})")
SEASON_RE = re.compile(r"^s[æa]son\s*\d{2}/\d{2}$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# DATAMODEL
# ---------------------------------------------------------------------------


@dataclass
class Match:
    url: str
    home: str
    away: str
    competition: str
    start_local: datetime
    venue: str = ""
    tv: str = ""
    time_confirmed: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def is_home(self) -> bool:
        return normalise(self.home) in FCK_NAMES

    @property
    def gets_ticket_alarm(self) -> bool:
        comp = self.competition.lower()
        if any(k in comp for k in NO_TICKET_ALARM_KEYWORDS):
            return False
        return self.is_home


# ---------------------------------------------------------------------------
# HJAELPEFUNKTIONER
# ---------------------------------------------------------------------------


def normalise(name: str) -> str:
    """Fjerner diakritiske tegn og dobbelte mellemrum, saa navne kan sammenlignes."""
    lowered = name.strip().lower()
    for src, dst in (("ø", "o"), ("æ", "a"), ("å", "a"), ("ö", "o"), ("ä", "a")):
        lowered = lowered.replace(src, dst)
    return re.sub(r"\s+", " ", lowered)


def fetch(url: str, session: requests.Session) -> BeautifulSoup:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def meta_content(soup: BeautifulSoup, *, name: str = "", prop: str = "") -> str:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return tag["content"].strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def page_lines(soup: BeautifulSoup) -> list[str]:
    return [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]


# ---------------------------------------------------------------------------
# INDSAMLING AF KAMPLINKS
# ---------------------------------------------------------------------------


def collect_match_urls(soup: BeautifulSoup) -> list[str]:
    """
    Finder alle kamplinks paa oversigtssiden, i dokumentraekkefoelge, uden dubletter.
    Baade relative links og absolutte links med og uden www accepteres.
    """
    urls: list[str] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        parsed = urlparse(anchor["href"])
        if parsed.scheme and parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc and parsed.netloc.lower() not in ALLOWED_HOSTS:
            continue

        path = parsed.path.rstrip("/")
        if not MATCH_PATH_RE.match(path):
            continue
        if path in seen:
            continue
        seen.add(path)
        urls.append(BASE_URL + path)

    return urls


# ---------------------------------------------------------------------------
# PARSING AF EN KAMPSIDE
# ---------------------------------------------------------------------------


def parse_teams_and_date(raw_title: str) -> tuple[str, str, datetime] | None:
    """
    og:title har formen "F.C. København vs. FC Midtjylland 2026-11-29".
    Returnerer (hjemmehold, udehold, dato uden klokkeslaet).
    """
    title = raw_title.strip()

    date_match = ISO_DATE_RE.search(title)
    if not date_match:
        return None
    year, month, day = (int(g) for g in date_match.groups())

    teams_part = title[: date_match.start()].strip()
    for separator in (" vs. ", " vs ", " mod "):
        if separator in teams_part:
            home, away = teams_part.split(separator, 1)
            return home.strip(), away.strip(), datetime(year, month, day)
    return None


def parse_description(raw_description: str) -> tuple[str, str]:
    """
    og:description har formen "3F Superliga · Sæson 26/27 · Parken - connected by 3".
    Returnerer (turnering, spillested). Spillestedet er tomt, hvis sidste led
    blot er saesonen.
    """
    parts = [p.strip() for p in raw_description.split("·") if p.strip()]
    if not parts:
        return "", ""

    competition = parts[0]
    venue = ""
    if len(parts) >= 2 and not SEASON_RE.match(parts[-1]):
        venue = parts[-1]
    return competition, venue


def parse_kickoff(soup: BeautifulSoup) -> tuple[int, int] | None:
    """
    Overskriften har formen "F.C. København mod FC Midtjylland, 29.11.2026 kl. 16.00".
    Mangler "kl. HH.MM", er kamptidspunktet ikke fastlagt endnu.
    """
    heading = soup.find("h1")
    if not heading:
        return None
    found = KICKOFF_RE.search(heading.get_text(" ", strip=True))
    if not found:
        return None
    hour, minute = int(found.group(1)), int(found.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def extract_venue_and_tv(lines: list[str]) -> tuple[str, str]:
    """
    Kampsiden har etiketterede felter: linjen "Stadion" efterfulgt af spillestedet,
    og kort efter linjen "TV" efterfulgt af kanalen. Soegningen efter "TV" starter
    forst efter "Stadion", saa menupunktet TV oeverst paa siden ikke rammes.
    """
    for index, line in enumerate(lines):
        if line != "Stadion" or index + 1 >= len(lines):
            continue

        venue = lines[index + 1]
        tv = ""
        for offset in range(index + 2, min(index + 12, len(lines) - 1)):
            if lines[offset] == "TV":
                candidate = lines[offset + 1]
                if is_plausible_tv(candidate):
                    tv = candidate
                break
        return venue, tv

    return "", ""


def is_plausible_tv(candidate: str) -> bool:
    if not candidate or len(candidate) > 60:
        return False
    lowered = candidate.lower()
    if lowered.startswith(("http", "/", "www.")):
        return False
    return not any(word in lowered for word in ("fanshop", "billetter", "business", "log ind"))


def parse_match_page(url: str, session: requests.Session) -> Match | None:
    soup = fetch(url, session)

    raw_title = meta_content(soup, prop="og:title") or (soup.title.get_text() if soup.title else "")
    parsed = parse_teams_and_date(raw_title)
    if not parsed:
        print(f"ADVARSEL: kunne ikke laese hold og dato paa {url}", file=sys.stderr)
        return None
    home, away, match_date = parsed

    raw_description = meta_content(soup, prop="og:description") or meta_content(
        soup, name="description"
    )
    competition, venue_from_meta = parse_description(raw_description)

    warnings: list[str] = []
    if not competition:
        warnings.append("turnering ikke fundet")

    venue, tv = extract_venue_and_tv(page_lines(soup))
    if not venue:
        venue = venue_from_meta
    if not venue:
        warnings.append("spillested ikke fundet")
    if not tv:
        warnings.append("tv-kanal ikke oplyst")

    kickoff = parse_kickoff(soup)
    time_confirmed = kickoff is not None
    hour, minute = kickoff if kickoff else TBC_PLACEHOLDER_TIME
    start = match_date.replace(hour=hour, minute=minute, tzinfo=LOCAL_TZ)

    return Match(
        url=url,
        home=home,
        away=away,
        competition=competition,
        start_local=start,
        venue=venue,
        tv=tv,
        time_confirmed=time_confirmed,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# ICS-OPBYGNING
# ---------------------------------------------------------------------------


def escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold(line: str) -> str:
    """Folder en ICS-linje til hoejst 75 oktetter, jf. RFC 5545."""
    if len(line.encode("utf-8")) <= 75:
        return line

    chunks: list[bytes] = []
    current = b""
    for char in line:
        char_bytes = char.encode("utf-8")
        if len(current) + len(char_bytes) > 75:
            chunks.append(current)
            current = b" " + char_bytes
        else:
            current += char_bytes
    chunks.append(current)
    return "\r\n".join(chunk.decode("utf-8") for chunk in chunks)


def to_utc_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def assign_uids(matches: list[Match]) -> dict[int, str]:
    """
    UID bindes til turnering, hold og hvilket moede i raekken det er, ikke til
    datoen. Naar en kamp flyttes, opdateres den samme begivenhed i stedet for at
    blive slettet og oprettet paa ny.
    """
    counters: dict[tuple[str, str, str], int] = defaultdict(int)
    uids: dict[int, str] = {}
    for match in sorted(matches, key=lambda m: m.start_local):
        key = (match.competition, normalise(match.home), normalise(match.away))
        counters[key] += 1
        raw = f"{key[0]}|{key[1]}|{key[2]}|{counters[key]}"
        uids[id(match)] = hashlib.sha1(raw.encode("utf-8")).hexdigest() + "@fck-kalender"
    return uids


def build_description(match: Match) -> str:
    lines = [
        f"Hjemmehold: {match.home}",
        f"Udehold: {match.away}",
        f"Turnering: {match.competition or 'Ikke oplyst'}",
        f"Spillested: {match.venue or 'Ikke oplyst'}",
        f"TV: {match.tv or 'Ikke oplyst'}",
    ]
    if not match.time_confirmed:
        placeholder = f"{TBC_PLACEHOLDER_TIME[0]:02d}:{TBC_PLACEHOLDER_TIME[1]:02d}"
        lines.append(
            f"OBS: kamptidspunktet er ikke bekræftet. {placeholder} er en pladsholder."
        )
    if match.gets_ticket_alarm:
        lines.append("Husk at frigive billet senest 36 timer før kampstart.")
    lines.append(f"Kampinfo: {match.url}")
    return "\n".join(lines)


def build_summary(match: Match) -> str:
    summary = f"{match.home} vs. {match.away}"
    if match.competition:
        summary += f" ({match.competition})"
    if not match.time_confirmed:
        summary += " [tidspunkt ikke bekræftet]"
    return summary


def event_lines(match: Match, uid: str, stamp: str) -> list[str]:
    end_local = match.start_local + MATCH_DURATION
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{to_utc_stamp(match.start_local)}",
        f"DTEND:{to_utc_stamp(end_local)}",
        f"SUMMARY:{escape(build_summary(match))}",
        f"DESCRIPTION:{escape(build_description(match))}",
        f"URL:{match.url}",
        "TRANSP:OPAQUE",
        f"STATUS:{'CONFIRMED' if match.time_confirmed else 'TENTATIVE'}",
    ]
    if match.competition:
        lines.append(f"CATEGORIES:{escape(match.competition)}")
    if match.venue:
        lines.append(f"LOCATION:{escape(match.venue)}")

    if match.gets_ticket_alarm and not SEPARATE_TICKET_EVENTS:
        hours = int(TICKET_ALARM_LEAD.total_seconds() // 3600)
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{escape(f'Frigiv billet: {match.home} vs. {match.away}')}",
            f"TRIGGER:-PT{hours}H",
            "END:VALARM",
        ]

    lines.append("END:VEVENT")
    return lines


def ticket_event_lines(match: Match, uid: str, stamp: str) -> list[str]:
    start = match.start_local - TICKET_ALARM_LEAD
    kickoff = match.start_local.strftime("%d/%m %H:%M")
    detail = f"Kampstart {kickoff}. {match.url}"
    return [
        "BEGIN:VEVENT",
        f"UID:ticket-{uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{to_utc_stamp(start)}",
        f"DTEND:{to_utc_stamp(start + timedelta(minutes=15))}",
        f"SUMMARY:{escape(f'Frigiv billet: {match.home} vs. {match.away}')}",
        f"DESCRIPTION:{escape(detail)}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def build_ics(matches: list[Match]) -> str:
    stamp = to_utc_stamp(datetime.now(timezone.utc))
    uids = assign_uids(matches)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//fck-kalender//DA//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape(CALENDAR_NAME)}",
        f"X-WR-CALDESC:{escape(CALENDAR_DESC)}",
        "X-WR-TIMEZONE:Europe/Copenhagen",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]

    for match in sorted(matches, key=lambda m: m.start_local):
        uid = uids[id(match)]
        lines += event_lines(match, uid, stamp)
        if match.gets_ticket_alarm and SEPARATE_TICKET_EVENTS:
            lines += ticket_event_lines(match, uid, stamp)

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


INDEX_HTML = """<!doctype html>
<html lang="da"><meta charset="utf-8"><title>FCK Kampe</title>
<body style="font-family:system-ui;max-width:40rem;margin:4rem auto;line-height:1.6">
<h1>FCK Kampe</h1>
<p>Abonnér på denne adresse i Google Kalender eller Outlook:</p>
<p><code id="u"></code></p>
<p><a href="fck.ics">fck.ics</a> - opdateret {stamp} UTC - {count} kampe.</p>
<script>document.getElementById('u').textContent =
  location.href.replace(/index\\.html$/,'') + 'fck.ics';</script>
</body></html>
"""


# ---------------------------------------------------------------------------
# HOVEDPROGRAM
# ---------------------------------------------------------------------------


def main() -> int:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "da,en;q=0.8"})

    print(f"Henter kampoversigt: {LIST_URL}")
    list_soup = fetch(LIST_URL, session)
    match_urls = collect_match_urls(list_soup)
    print(f"Fandt {len(match_urls)} kamplinks.")

    if not match_urls:
        print(
            "FEJL: ingen kamplinks fundet. Sidens struktur er sandsynligvis aendret.",
            file=sys.stderr,
        )
        return 1

    matches: list[Match] = []
    failures = 0
    for url in match_urls:
        try:
            match = parse_match_page(url, session)
        except requests.RequestException as exc:
            print(f"ADVARSEL: kunne ikke hente {url}: {exc}", file=sys.stderr)
            failures += 1
            continue
        if match is None:
            failures += 1
            continue
        matches.append(match)
        flag = "H" if match.is_home else "U"
        note = f" [{', '.join(match.warnings)}]" if match.warnings else ""
        print(
            f"  {flag} {match.start_local:%d/%m %H:%M} {match.home} vs. {match.away}"
            f" | {match.competition or '?'} | {match.venue or '?'} | {match.tv or '?'}{note}"
        )
        time.sleep(REQUEST_PAUSE_SECONDS)

    if not matches:
        print("FEJL: ingen kampe kunne laeses. Kalenderen opdateres ikke.", file=sys.stderr)
        return 1

    if failures > len(match_urls) / 2:
        print(
            f"FEJL: {failures} af {len(match_urls)} kampsider kunne ikke laeses. "
            "Kalenderen opdateres ikke.",
            file=sys.stderr,
        )
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ICS_PATH.write_text(build_ics(matches), encoding="utf-8")
    (OUTPUT_DIR / "index.html").write_text(
        INDEX_HTML.format(
            stamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), count=len(matches)
        ),
        encoding="utf-8",
    )

    home_alarms = sum(1 for m in matches if m.gets_ticket_alarm)
    tbc = sum(1 for m in matches if not m.time_confirmed)
    print(
        f"\nSkrev {ICS_PATH} med {len(matches)} kampe, "
        f"{home_alarms} billetpaamindelser, {tbc} uden bekraeftet tidspunkt."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
