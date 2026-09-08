# FCK-kalender

Selvopdaterende kalenderfeed med F.C. Københavns kommende kampe, hentet fra
[fck.dk/kommende-kampe](https://www.fck.dk/kommende-kampe) og de enkelte kampsider.

GitHub Actions kører scriptet en gang om ugen, skriver `public/fck.ics` og udgiver
filen på GitHub Pages. Google Kalender og Outlook abonnerer på adressen.

## Hvad hver begivenhed indeholder

| Felt | Indhold |
| --- | --- |
| Titel | Hjemmehold vs. udehold (turnering) |
| Sted | Spillested fra kampsiden, for eksempel Parken - connected by 3 |
| Tid | Kampstart i Europe/Copenhagen, 2 timers varighed |
| Beskrivelse | Hjemmehold, udehold, turnering, spillested, tv-kanal, link til kampsiden |
| Alarm | 36 timer før kampstart, kun ved hjemmekampe, ikke ved træningskampe |

Alle fire turneringer indgår: 3F Superliga, Betano Pokalen, UEFA Conference League
og træningskampe.

## Opsætning, engangsarbejde

1. Opret et nyt repository på GitHub, for eksempel `fck-kalender`. Standardgrenen
   skal hedde `main`. Planlagte workflows kører kun fra standardgrenen.
2. Læg alle filer fra denne mappe i repositoriet og push.
3. Gå til **Settings, Pages** og sæt **Source** til **GitHub Actions**.
4. Gå til **Actions**, vælg **Opdater FCK-kalender** og kør **Run workflow** manuelt
   første gang.
5. Åbn kørslens log. Den lister hver kamp med hjemmehold, udehold, spillested og
   tv-kanal. Kontroller den liste mod fck.dk, inden du abonnerer.
6. Feedadressen er `https://<brugernavn>.github.io/fck-kalender/fck.ics`.

### Abonner i Google Kalender

Andre kalendere, plus-ikonet, **Fra URL**, indsæt adressen, **Tilføj kalender**.

### Abonner i Outlook

**Tilføj kalender**, **Abonner fra internettet**, indsæt adressen, giv kalenderen
et navn og importer.

Bemærk at abonnementet er skrivebeskyttet. Ændringer sker udelukkende i feedet.

## Sådan holdes kalenderen opdateret

Workflowet kører mandag kl. 06:00 UTC og igen ved hvert push. Det kan altid startes
manuelt under **Actions**.

Hver kørsel skriver et commit, også når kampprogrammet er uændret. Det er med vilje:
i et offentligt repository deaktiverer GitHub planlagte workflows automatisk, når der
ikke har været aktivitet i 60 dage. Commit'et holder planen i live.

Kalenderklienter henter feedet på deres egen kadence, typisk et sted mellem 8 og 24
timer. En flyttet kamp er derfor synlig på din telefon inden for cirka et døgn efter
den ugentlige kørsel, ikke øjeblikkeligt.

En kamps UID er bundet til turnering, hold og hvilket indbyrdes møde det er, ikke til
datoen. Når en kamp flyttes, opdateres den eksisterende begivenhed i stedet for at
blive slettet og oprettet på ny.

## Kampe uden bekræftet tidspunkt

fck.dk viser kampe uden fastlagt kampstart som kl. 00:00. Otte kampe stod sådan den
8. september 2026, blandt andet FC Nordsjælland ude den 22. november og AGF ude den
28. februar. Scriptet lægger dem kl. 15:00, markerer titlen med
`[tidspunkt ikke bekræftet]`, sætter `STATUS:TENTATIVE` og skriver en note i
beskrivelsen. Når klubben fastlægger tidspunktet, retter næste kørsel begivenheden.

## Indstillinger

Alle knapper ligger øverst i `build_calendar.py`:

| Variabel | Standard | Betydning |
| --- | --- | --- |
| `MATCH_DURATION` | 2 timer | Begivenhedens længde |
| `TICKET_ALARM_LEAD` | 36 timer | Varsel for billetfrigivelse |
| `TBC_PLACEHOLDER_TIME` | 15:00 | Pladsholder for ubekræftede kampe |
| `NO_TICKET_ALARM_KEYWORDS` | træningskamp | Turneringer uden billetpåmindelse |
| `SEPARATE_TICKET_EVENTS` | `False` | Se nedenfor |

`SEPARATE_TICKET_EVENTS` er en reserveløsning. Alarmen ligger som aftalt inde i selve
kampbegivenheden, men enkelte kalenderklienter viser ikke alarmer fra abonnerede feeds,
fordi feedet ikke er din egen kalender. Hvis påmindelsen ikke udløses efter den første
hjemmekamp, så sæt variablen til `True` og push. Så oprettes der i stedet en separat
15 minutters begivenhed med titlen "Frigiv billet" 36 timer før hver hjemmekamp, og den
vises uanset klientens håndtering af alarmer.

## Test

`python test_parser.py` kører uden netadgang. Testene dækker linkindsamling,
titelparsing, spillested, tv-kanal i to forskellige HTML-layouts, hjemme- og udebane,
udeladelse af påmindelse ved træningskampe, pladsholder for ubekræftede tidspunkter,
UTC-konvertering, linjefoldning efter RFC 5545 og stabile UID'er. Workflowet kører
testene før hver opdatering.

Parseren er verificeret mod gengivet sidestruktur, ikke mod fck.dk direkte, fordi
udviklingsmiljøet ikke havde netadgang til domænet. Den første kørsel i Actions er
derfor den egentlige prøve. Scriptet fejler med det samme og lader den eksisterende
kalender være i fred, hvis oversigtssiden ikke giver kamplinks, eller hvis mere end
halvdelen af kampsiderne ikke kan læses.

## Hvis fck.dk ændrer sidestruktur

Scriptet læser tre ting fra hver kampside:

1. `og:title`, som indeholder hold, dato, tidspunkt og turnering. Falder tilbage på
   `<title>`.
2. `geo.placename`, som indeholder spillestedet.
3. Linjen med spillested og tv-kanal adskilt af en lodret streg.

Punkt 1 og 2 er metatags og er robuste. Punkt 3 læses fra sidens tekst og er det
svageste led. Hvis tv-kanalen forsvinder fra kalenderen, står feltet som "Ikke oplyst",
og resten af begivenheden er upåvirket.

## Alternativ uden opsætning

Klubben udgiver selv et feed på `webcal://www.fck.dk/fck.ics`, linket fra
oversigtssiden som "Add all matches to calendar". Det opdaterer automatisk, men giver
ingen kontrol over tv-kanal, spillestedstekst eller betingede påmindelser, og det er
derfor ikke brugt her.
