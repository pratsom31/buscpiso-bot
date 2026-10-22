# BuscPiso Bot 🏠

Your Barcelona flat-hunting bot. Watches rental agencies (inmobiliarias) and
portals for flats matching **your** criteria and sends every new one to your
Telegram (works great in a group with your partner). Expired, seasonal,
touristic and shared listings are filtered out automatically.

Free to run (GitHub Actions + your Mac for the bot-hostile portals), zero AI
tokens — all filtering is deterministic. MIT licensed.

## Configure it for you

```bash
python bot.py --setup
```

An interactive wizard asks your criteria and writes `config.json`:

| Setting | What it controls |
|---|---|
| `max_price` / `min_size` / `max_rooms` | your non-negotiables (rooms: 0 = studio) |
| `rental_type` | `long_term` (1+ year contracts only) or `any` |
| `zones` | limit to barrios, e.g. `["gracia", "eixample"]` — empty = all BCN |
| `require_furnished` | only listings that mention furnished |
| `pets_info` | adds a 🐾 tag when a listing says pets are allowed |
| `avoid_platforms` | short-stay platforms to always skip (Uniplaces, Spotahome…) |
| `avoid_keywords` | your personal deal-breakers, e.g. `"sin ascensor"` |

Commit + push `config.json` and the cloud runs use it too. Env vars
(`MAX_PRICE`, `MIN_SIZE`, `MAX_ROOMS`, `RENTAL_TYPE`) override the file.

See [SETUP-CLOUD.md](SETUP-CLOUD.md) for the free 24/7 cloud setup
(GitHub Actions + optional Telegram `/sweep` trigger via Cloudflare Worker).

## Sources (agencies first, then portals)

| Source | Type | Long-term guarantee |
|---|---|---|
| Housfy | agency/proptech | only does standard LAU (long-term) rentals |
| ShBarcelona | agency | scraped from its "yearly" department only; commercial units filtered out |
| Loca Barcelona | agency | its "long-term rental" category only |
| Finques Teixidor | agency (administrador de fincas) | classic long-term landlord stock; parkings/locales filtered out |
| Idealista | portal | portal's own `larga temporada` filter (server-side) |
| Fotocasa | portal | per-listing `isTemporaryRental` + `IS_SHARED` flags (5 pages walked; ~90% of new cheap listings are seasonal and get dropped) |
| Habitaclia | portal | seasonal stock lives in a separate section + keyword blacklist |
| Pisos.com | portal | keyword blacklist |

Keyword blacklist — screened against the **whole card** (title + description +
badges/labels like pisos.com's "Temporada" tag + advertiser name): temporada,
temporal, month-capped contracts (máximo/contrato de 1-11 meses — the seasonal
11-month loophole), corta/media estancia, por meses, vacacional, turístico,
short/mid-term, seasonal, compartido, coliving, and short-stay platforms
(Uniplaces, Renteazily, Spotahome, HousingAnywhere, Badi). 12+ month contracts
are treated as normal yearly leases and pass.

Note on Idealista: it is DataDome-protected, so it works when the bot runs
locally (Safari TLS fingerprint) but will likely 403 from cloud
(datacenter) IPs. That failure is isolated — the run continues and you simply don't get
Idealista results in cloud mode.

## Top-10 inmobiliarias coverage

Every significant BCN rental agency was probed for a crawlable website
(~30 candidates tested). Where the agency's own site can't be crawled
(JavaScript-only, no cheap stock), its listings still reach you because
agencies syndicate to Fotocasa/Habitaclia/Idealista, which the bot walks
every run.

| # | Agency | How it's covered |
|---|---|---|
| 1 | [Housfy](https://housfy.com/alquiler-pisos/barcelona/barcelona) | ✅ crawled directly |
| 2 | [ShBarcelona](https://www.shbarcelona.com/apartments-for-rent/long-term) | ✅ crawled directly (yearly dept) |
| 3 | [Loca Barcelona](https://www.locabarcelona.com/en/property-status/long-term-rental/) | ✅ crawled directly (long-term category) |
| 4 | [Finques Teixidor](https://www.finquesteixidor.com/es/alquiler-barcelona.cfm) | ✅ crawled directly |
| 5 | [Forcadell](https://www.forcadell.com/venta-alquiler-viviendas/) | JS-only site → via portal syndication |
| 6 | [Vivendex](https://www.vivendex.com/) | JS-only site → via portal syndication |
| 7 | [Tecnocasa](https://www.tecnocasa.es/alquiler/piso/cataluna/barcelona/barcelona.html) | JS-only site → via portal syndication |
| 8 | [Amat Immobiliaris](https://www.amat.es/) | JS-only site → via portal syndication |
| 9 | [Núñez i Navarro](https://www.nyn.es/es/alquiler/pisos/barcelona) | SPA, not crawlable → check manually (direct landlord, own buildings) |
| 10 | [aProperties](https://www.aproperties.es/pisos-alquiler-barcelona) | JS-only; stock rarely <1000€ → via portal syndication |

Probed and excluded on purpose: Solvia (zero BCN residential rental stock —
verified via their API), Fincas Almendros (no BCN stock under budget),
Suitelife (expat pricing, all >1000€), Century21/Remax (JS + captcha),
Finques Chicote / Martell / Garví / Bou / El Pallars (no crawlable catalogue),
yaencontre (bot-blocked), enalquiler (same Adevinta stock as Fotocasa).

## Deploy on GitHub Actions (free)

The bot runs in the cloud on GitHub Actions, on a schedule you choose. The
default is **every 6 hours**: change the `cron` line in
`.github/workflows/sweep.yml` to run it more or less often.

1. **Telegram bot**: message **@BotFather** → `/newbot` → copy the token.
   Then message your new bot anything (so it can reply to you).
2. **Chat id**: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read
   `"chat":{"id":XXXXXXX}` from the response.
3. Fork or copy this repo, then go to **Settings → Secrets and variables →
   Actions** and add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. Open the **Actions** tab, enable workflows, and press **Run workflow** on
   "BCN rental sweep" to test it. The first run sends the current matches
   (capped per run; overflow follows next run).

Dedupe memory lives in `state.cloud.json`, which the workflow commits back to
the repo after each run. See [SETUP-CLOUD.md](SETUP-CLOUD.md) for the optional
Telegram `/sweep` trigger via a Cloudflare Worker.

## Split mode: cloud + the Mac for bot-hostile portals

Idealista and Fotocasa block datacenter IPs, so the cloud run skips them
(`SOURCES: "-idealista,-fotocasa"` in `sweep.yml`). Cover them from your Mac
by double-clicking one of the `run-*.command` files in the bot folder, or with
`.venv/bin/python bot.py --sources idealista`. The cloud and the Mac each keep
their own dedupe memory, and since their sources don't overlap you never get
duplicate Telegram messages.

## Sharing with a partner (group chat)

1. In Telegram, create a new group with your partner.
2. Add the bot to the group (search its @username when adding members).
3. Send any message in the group, then get the group's chat id with
   `.venv/bin/python bot.py --chat-id` — group ids are **negative** numbers
   (e.g. `-4871...`).
4. Put that id in `TELEGRAM_CHAT_ID` (both in local `.env` and in the repo's
   Actions secrets). All listings now arrive in the shared group.

## Run locally instead (optional)

```bash
cd ~/Desktop/bcn-rental-bot
# fill .env with TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
.venv/bin/python bot.py --chat-id   # discover chat id (alternative to step 2)
.venv/bin/python bot.py --test      # Telegram connectivity check
.venv/bin/python bot.py --dry-run   # print matches, store/send nothing
.venv/bin/python bot.py             # real run (state kept in state.json)
./install.sh                        # every-5h launchd schedule on this Mac
```

## Expired-listing screening

Right before a listing is sent to Telegram, the bot opens its detail page and
drops it silently if the ad is gone: HTTP 404/410, the portal redirecting off
the ad, or "ya no está disponible / Alquilado / Reservado" markers. Fotocasa
uses only the redirect signal (its pages embed all expiry texts in JS), and
Idealista is exempt (detail pages are DataDome-blocked; its search results
only contain active ads anyway). Fotocasa messages also carry a 📅 "hace X h"
freshness stamp. Disable the whole check with env var `VERIFY_ALIVE=0`.
What this cannot catch: ads that are still published but ancient/bait.

## Notes

- Criteria changes = edit `config.json` (cloud and local) or `.env` (local).
  Delete `state.cloud.json` (or `state.json` locally) to start fresh.
- "? hab." in a message means the site didn't state bedrooms on the card
  (common for studios); every other filter still passed.
- If a scraper errors persistently (site redesign), you'll get a Telegram
  warning when half or more sources fail; single-source failures just appear
  in the Actions run logs.
- Scrape volume is tiny (about 10 requests per run) and for personal use — keep it that
  way; portals' terms discourage scraping.
