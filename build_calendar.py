#!/usr/bin/env python3
"""
Bygger en ICS-kalender med F.C. Koebenhavns kommende kampe.

Kilde: https://www.fck.dk/kommende-kampe og de enkelte kampsider paa fck.dk.
Output: public/fck.ics (plus public/index.html).

Koeres ugentligt af GitHub Actions. Se README.md.
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
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# KONFIGURATION
# ---------------------------------------------------------------------------

BASE_URL = "https://www.fck.dk"
LIST_URL = f"{BASE_URL}/kommende-kampe"

CALENDAR_NAME = "FCK Kampe"
CALENDAR_DESC = "F.C. Københavns kommende kampe, opdateret ugentligt fra fck.dk"

LOCAL_TZ = ZoneInfo("Europe/Copenhagen")

# Kampens varighed i kalenderen.
MATCH_DURATION = timedelta(hours=2)

# Kampe uden bekraeftet kamptidspunkt vises paa fck.dk som kl. 00:00.
# De laegges i stedet paa denne pladsholdertid og markeres som ikke bekraeftet.
TBC_SOURCE_TIME = (0, 0)
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
FCK_NAMES = {"f.c. kobenhavn", "f.c. kobenhavn", "fc kobenhavn", "f.c. koebenhavn", "fck"}

USER_AGENT = "fck-kalender/1.0 (personal calendar feed; +https://github.com)"
REQUEST_PAUSE_SECONDS = 1.0
REQUEST_TIMEOUT = 30

OUTPUT_DIR = Path("public")
ICS_PATH = OUTPUT_DIR / "fck.ics"

MATCH_HREF_RE = re.compile(r"^/kamp/\d{2}-\d{2}-\d{2}/[a-z0-9\-]+$")
DATETIME_RE = re.compile(r"(\d{2})/(\d{2})\s+(\d{4})\s+(\d{1,2}):(\d{2})")


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
        return normalise_team(self.home) in FCK_NAMES

    @property
    def gets_ticket_alarm(self) -> bool:
        comp = self.competition.lower()
        if any(k in comp for k in NO_TICKET_ALARM_KEYWORDS):
            return False
        return self.is_home


# ---------------------------------------------------------------------------
# HJAELPEFUNKTIONER
# ---------------------------------------------------------------------------


def normalise_team(name: str) -> str:
    """Fjerner diakritiske tegn og mellemrum saa holdnavne kan sammenlignes."""
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


# ---------------------------------------------------------------------------
# INDSAMLING AF KAMPLINKS
# ---------------------------------------------------------------------------


def collect_match_urls(soup: BeautifulSoup) -> list[str]:
    """Finder alle kamplinks paa oversigtssiden, i dokumentraekkefoelge, uden dubletter."""
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].split("?")[0].rstrip("/")
        if href.startswith(BASE_URL):
            href = href[len(BASE_URL) :]
        if not MATCH_HREF_RE.match(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(BASE_URL + href)
    return urls


# ---------------------------------------------------------------------------
# PARSING AF EN KAMPSIDE
# ---------------------------------------------------------------------------


def parse_title(raw_title: str) -> tuple[str, str, str, datetime] | None:
    """
    Forventet format paa kampsidens og:title:
        "F.C. Koebenhavn vs. AGF 2026-10-18  | 18/10 2026 18:00 | 3F Superliga"
    Returnerer (hjemmehold, udehold, turnering, naiv lokal kampstart).
    """
    parts = [p.strip() for p in raw_title.split("|") if p.strip()]
    if len(parts) < 3:
        return None

    # Sidste led kan vaere sitenavnet, hvis vi faldt tilbage paa <title>.
    if normalise_team(parts[-1]) in FCK_NAMES and len(parts) >= 4:
        parts = parts[:-1]

    teams_part, datetime_part, competition = parts[0], parts[1], parts[2]

    match = DATETIME_RE.search(datetime_part)
    if not match:
        return None
    day, month, year, hour, minute = (int(g) for g in match.groups())

    teams_part = re.sub(r"\s*\d{4}-\d{2}-\d{2}\s*$", "", teams_part).strip()
    if " vs. " in teams_part:
        home, away = teams_part.split(" vs. ", 1)
    elif " vs " in teams_part:
        home, away = teams_part.split(" vs ", 1)
    else:
        return None

    start = datetime(year, month, day, hour, minute)
    return home.strip(), away.strip(), competition.strip(), start


def extract_tv(soup: BeautifulSoup, venue: str) -> str:
    """
    Paa kampsiden staar spillested og tv-kanal paa samme linje adskilt af en lodret streg,
    for eksempel "Parken - connected by 3 | TV3+ / Viaplay".
    Elementgraensen kan ligge forskellige steder, saa begge layouts haandteres.
    """
    if not venue:
        return ""

    lines = [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]
    venue_norm = normalise_team(venue)

    for index, line in enumerate(lines):
        line_norm = normalise_team(line)
        if not line_norm.startswith(venue_norm):
            continue

        # Layout A: hele linjen i eet element.
        if "|" in line:
            candidate = line.split("|", 1)[1].strip()
            if is_plausible_tv(candidate):
                return candidate

        # Layout B: spillested, streg og kanal i separate elementer.
        window = " ".join(lines[index + 1 : index + 4])
        if "|" in window:
            candidate = window.split("|", 1)[1].strip()
            candidate = candidate.split("  ")[0].strip()
            if is_plausible_tv(candidate):
                return candidate

    return ""


def is_plausible_tv(candidate: str) -> bool:
    if not candidate or len(candidate) > 60:
        return False
    if "\n" in candidate:
        return False
    # Udelukker navigationstekst der er sluppet med.
    lowered = candidate.lower()
    return not any(word in lowered for word in ("koeb billet", "køb billet", "go to match", "menu"))


def parse_match_page(url: str, session: requests.Session) -> Match | None:
    soup = fetch(url, session)

    raw_title = meta_content(soup, prop="og:title") or (soup.title.get_text() if soup.title else "")
    parsed = parse_title(raw_title)
    if not parsed:
        print(f"ADVARSEL: kunne ikke laese titel paa {url}", file=sys.stderr)
        return None

    home, away, competition, start = parsed
    warnings: list[str] = []

    time_confirmed = (start.hour, start.minute) != TBC_SOURCE_TIME
    if not time_confirmed:
        start = start.replace(hour=TBC_PLACEHOLDER_TIME[0], minute=TBC_PLACEHOLDER_TIME[1])

    venue = meta_content(soup, name="geo.placename")
    if not venue:
        warnings.append("spillested ikke fundet")

    tv = extract_tv(soup, venue)
    if not tv:
        warnings.append("tv-kanal ikke fundet")

    return Match(
        url=url,
        home=home,
        away=away,
        competition=competition,
        start_local=start.replace(tzinfo=LOCAL_TZ),
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
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line

    chunks: list[bytes] = []
    current = b""
    limit = 75
    for char in line:
        char_bytes = char.encode("utf-8")
        if len(current) + len(char_bytes) > limit:
            chunks.append(current)
            current = b" " + char_bytes
            limit = 75
        else:
            current += char_bytes
    chunks.append(current)
    return "\r\n".join(chunk.decode("utf-8") for chunk in chunks)


def to_utc_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def assign_uids(matches: list[Match]) -> dict[int, str]:
    """
    UID bindes til turnering, hold og hvilket moede i raekken det er, ikke til datoen.
    Naar en kamp flyttes, opdateres den samme begivenhed i stedet for at blive
    slettet og oprettet paa ny.
    """
    counters: dict[tuple[str, str, str], int] = defaultdict(int)
    uids: dict[int, str] = {}
    for index, match in enumerate(sorted(matches, key=lambda m: m.start_local)):
        key = (match.competition, normalise_team(match.home), normalise_team(match.away))
        counters[key] += 1
        raw = f"{key[0]}|{key[1]}|{key[2]}|{counters[key]}"
        uids[id(match)] = hashlib.sha1(raw.encode("utf-8")).hexdigest() + "@fck-kalender"
    return uids


def build_description(match: Match) -> str:
    lines = [
        f"Hjemmehold: {match.home}",
        f"Udehold: {match.away}",
        f"Turnering: {match.competition}",
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
    summary = f"{match.home} vs. {match.away} ({match.competition})"
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
        f"CATEGORIES:{escape(match.competition)}",
    ]
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
        print("FEJL: ingen kamplinks fundet. Sidens struktur er sandsynligvis aendret.", file=sys.stderr)
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
            f" | {match.competition} | {match.venue or '?'} | {match.tv or '?'}{note}"
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
