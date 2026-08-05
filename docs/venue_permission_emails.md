# Permission emails to venues

Drafts for asking each venue whether diytracker may read their public event
pages. One per venue, in the language that venue's own site uses. Copy the
subject and body into a mail client and send from `info@diytracker.ch` — these
are one-off outreach mails, so there is deliberately no code path that sends
them.

Everything factual in the templates is true of the scraper as it stands
(`diytracker/services/scrape_events.py`): it sends
`diytracker (+https://diytracker.ch; lucaggett@gmail.com)` as its User-Agent,
reads `robots.txt` before every request and skips what is disallowed, waits
between requests, and stops immediately on a `403`. Don't edit those claims
without changing the code to match.

**Please read the per-venue notes below before sending.** Three of the
addresses need a judgement call, and one venue is being asked for permission it
has already refused in machine-readable form.

---

## Template — German

> **Betreff:** Kurze Frage: dürfen wir euer Programm auf diytracker.ch verlinken?
>
> Hallo zusammen
>
> Ich bin Luc und betreibe diytracker.ch — einen nicht-kommerziellen
> Veranstaltungskalender für DIY-, Punk-, Rock- und Metal-Konzerte in der
> Schweiz. Kein Ticketverkauf, keine Werbung, keine Firma dahinter.
>
> Eure Konzerte werden bei uns bisher von Hand eingetragen. Ich würde das gerne
> automatisieren und dafür einmal alle paar Stunden eure Programmseite
> (`{{PROGRAMM_URL}}`) auslesen — also genau das, was auch jede Besucherin im
> Browser sieht. Verlinkt wird immer auf euch.
>
> Konkret heisst das: unser Skript meldet sich mit einem eindeutigen
> Erkennungsnamen (`diytracker`), hält sich an eure `robots.txt`, macht Pausen
> zwischen den Anfragen und hört sofort auf, wenn euer Server das signalisiert.
> Es ist eine Handvoll Anfragen pro Tag, keine Last für euch.
>
> Ist das für euch in Ordnung? Ein kurzes "ja" oder "nein" reicht mir völlig.
> Bei "nein" trage ich euch dauerhaft aus, ohne Rückfragen — und falls euch
> eine andere Lösung lieber ist (ihr schickt uns die Daten, oder wir lassen es
> ganz), sagt einfach Bescheid.
>
> Herzliche Grüsse
> Luc
> diytracker.ch · info@diytracker.ch

## Template — Français

> **Objet :** Petite question : pouvons-nous relayer votre programme sur diytracker.ch ?
>
> Bonjour
>
> Je m'appelle Luc et je gère diytracker.ch, un agenda non commercial des
> concerts DIY, punk, rock et metal en Suisse. Pas de billetterie, pas de
> publicité, aucune société derrière.
>
> Vos concerts sont pour l'instant saisis à la main chez nous. J'aimerais
> automatiser ça et lire votre page de programme (`{{PROGRAMM_URL}}`) toutes les
> quelques heures — exactement ce que voit n'importe quel visiteur dans son
> navigateur. Le lien renvoie toujours vers vous.
>
> Concrètement : notre script s'identifie clairement (`diytracker`), respecte
> votre `robots.txt`, espace ses requêtes et s'arrête immédiatement si votre
> serveur le demande. Cela représente quelques requêtes par jour, aucune charge
> pour vous.
>
> Est-ce que ça vous convient ? Un simple "oui" ou "non" me suffit. Si c'est
> non, je vous retire définitivement, sans insister — et si vous préférez une
> autre solution (vous nous envoyez les données, ou on laisse tomber),
> dites-le-moi.
>
> Cordialement
> Luc
> diytracker.ch · info@diytracker.ch

---

## Ready to send

`{{PROGRAMM_URL}}` is filled in per venue below.

| # | Venue | To | Lang | Programme URL |
| --- | --- | --- | --- | --- |
| 1 | Die Cafete, Bern | cafete@cafete.ch | de | https://cafete.ch/ |
| 2 | KUZEB, Bremgarten | office@kuzeb.ch | de | https://www.kuzeb.ch/events |
| 3 | Post Squat, Zürich | postevents@systemli.org | de | https://post.zureich.rip/kalender-alle/ |
| 4 | Provitreff, Zürich | info@provitreff.ch | de | https://provitreff.ch/ |
| 5 | Rümpeltum, St. Gallen | ruempeltum@mail.ch | de | https://rumpeltum.ch/programm/ |
| 6 | Eldorado, Biel/Bienne | eldorado-bielbienne@gmx.ch | de | https://eldoradobielbienne.ch/agenda/ |
| 7 | Bad Bonn, Düdingen | info@badbonn.ch | de | https://club.badbonn.ch/ |
| 8 | Kaschemme, Basel | kontakt@kaschemme.ch | de | https://www.kaschemme.ch/programm |
| 9 | Werkk Kulturlokal, Baden | love@werkk-baden.ch | de | https://werkk-baden.ch/agenda/programm/ |
| 10 | Gare de Lion, Wil | promo@garedelion.ch | de | https://garedelion.ch/events/ |
| 11 | Safari Bar, Zürich | info@safaribar.ch | de | https://www.safaribar.ch/ |
| 12 | TapTab, Schaffhausen | sekretariat@taptab.ch | de | https://taptab.ch/ |
| 13 | Café Bar Treppenhaus, Rorschach | info@treppenhaus.ch | de | https://treppenhaus.ch/events/ |
| 14 | Nouveau Monde, Fribourg | info@nouveaumonde.ch | **fr** | https://www.nouveaumonde.ch/agenda/ |
| 15 | QuaiDuBas30, Biel/Bienne | contact@quaidubas30.ch | **fr** | https://quaidubas30.ch/fr/events/ |

Notes on individual sends:

- **Bad Bonn** (7) publishes in German and French on the same pages. German is
  fine; switch to the French template if you'd rather.
- **QuaiDuBas30** (15) mirrors its site in de/fr/en. The collective writes
  mainly in French, so the French template is the better opener — the scraper
  reads the German mirror only because the events are identical and German is
  the tracker's base language.
- **Eldorado** (6) is in bilingual Biel but its site is German-only.
- **Gare de Lion** (10): `promo@` is the only address in the Impressum. It's a
  press/promo mailbox, so expect a forward rather than a direct answer.

## Send with care

- **Alte Post, Zürich** — `altepostseebach@proton.me`, German template,
  programme URL `https://altepost.zureich.rip/events/`. **The address is not
  published in plain text**; the site hides it behind an obfuscated
  click-to-reveal link, which decodes to the above. That obfuscation is a
  deliberate anti-harvesting measure, so the address is unverified and writing
  to it is arguably going around their choice. Worth confirming in person, or
  via the Post Squat contact, before mailing — the two spaces cross-link and
  likely share people.

- **Horstklub, Kreuzlingen** — programme URL `https://horstklub.ch/`, German
  template. **No general contact address is published.** The only address on
  the site is `bestellung@horstklub.ch`, which is explicitly for merch orders,
  and the awareness contact is published as an image precisely so it can't be
  scraped. Ask via Instagram/Facebook (`@horstklub`) rather than mailing the
  orders inbox. Note the irony worth being straight about: their `events.csv`
  is the single most convenient source in this whole set, and they are the
  hardest venue to ask.

- **Photobastei, Zürich** — `info@photobastei.ch`, German template, programme
  URL `https://www.photobastei.ch/site/program`. **There is no scraper for this
  venue and there must not be one until they reply yes.** Their `robots.txt` is
  `User-agent: * / Disallow: /` for the entire site, which is a clear machine-
  readable "no" that the scraper already obeys. Because of that, the mail
  should say so plainly rather than using the template as-is — replace the
  third paragraph with:
  > Eure robots.txt sperrt aktuell die ganze Seite für automatische Zugriffe,
  > und daran halten wir uns selbstverständlich — wir lesen bei euch nichts
  > aus. Deshalb frage ich lieber direkt: wäre es für euch in Ordnung, wenn wir
  > eure Programmseite auslesen? Wenn ja, würde ich mich freuen; wenn nein,
  > bleibt einfach alles wie es ist.

## No address found

These have no contact to write to. Listed so the next pass doesn't re-research
them:

| Venue | Why |
| --- | --- |
| Grüntal, Winterthur | No website; Facebook page only. |
| Bahnhöfli, Biel/Bienne | No website; Instagram/Facebook only. |
| Toms Beer Box, Chur | No website. Only a phone number is public (`toms.ch` is the TOMS shoe shop, not the bar). |
| Picadilly, Brugg | `info@p-i-c.ch` exists, but there is nothing to scrape — the programme is posted as flyer images. Only worth mailing if you want to ask them to publish text. |
| Moshpit Club, Naters | `bastian@moshpit.ch` / `moshpit@gmx.ch` exist, but the site's programme page is empty and shows live on Facebook. Same caveat as Picadilly. |
| Château d'Erguël, Sonvilier | No venue site — only the Sonvilier municipal tourism page (`administration@sonvilier.ch`). Concerts there are really the Toxoplasmose festival. |
| Röonda, Zürich | Could not be identified at all. Check the venue name with whoever entered it before anything else. |
