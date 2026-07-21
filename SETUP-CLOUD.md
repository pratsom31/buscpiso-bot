# Cloud setup — free, runs when your Mac is off

Two phases. Phase 1 (GitHub Actions) is the important half: it runs the sweep
every 2 hours in the cloud, no Mac needed, $0. Phase 2 (Cloudflare Worker) adds
a Telegram `/sweep` button. Idealista + Fotocasa can't run in any cloud
(datacenter IPs get blocked) — keep sweeping those from the Mac.

---

## Phase 1 — GitHub Actions (do this first)

1. **Create a repo**: on github.com click **New repository** → name it
   `bcn-rental-bot` → **Public** (unlimited free Actions minutes; nothing
   secret is in the code) → Create. Don't add a README (we have one).

2. **Push the code** — in Terminal:
   ```bash
   cd ~/Desktop/bcn-rental-bot
   git remote add origin https://github.com/<YOUR_USERNAME>/bcn-rental-bot.git
   git push -u origin main
   ```
   (`.env` and your local `state.json` are gitignored — the token never leaves
   your Mac.)

3. **Add the secrets**: repo → **Settings** → **Secrets and variables** →
   **Actions** → **New repository secret**, add two:
   - `TELEGRAM_BOT_TOKEN` = your bot token
   - `TELEGRAM_CHAT_ID` = `-5487066468`

4. **Enable + test**: repo → **Actions** tab → enable workflows if prompted →
   click **BCN rental sweep** → **Run workflow**. First run installs deps and
   sweeps; new listings arrive in the group. After that it runs every 2h on its
   own.

Note: GitHub disables scheduled workflows after 60 days of **no repo activity**
— but each run commits `state.cloud.json`, which counts as activity, so it
keeps itself alive.

---

## Phase 2 — Cloudflare Worker (the `/sweep` button)

1. **GitHub token for the Worker**: github.com → your avatar → **Settings** →
   **Developer settings** → **Personal access tokens** → **Fine-grained
   tokens** → **Generate new token**. Repository access: only `bcn-rental-bot`.
   Permissions: **Actions → Read and write**, **Contents → Read**. Generate and
   copy the token.

2. **Create the Worker**: dash.cloudflare.com (free signup, no card) →
   **Workers & Pages** → **Create** → **Create Worker** → name it
   `buscpiso-trigger` → Deploy → **Edit code** → paste all of
   `cloudflare-worker.js` → **Deploy**.

3. **Add Worker secrets**: the Worker → **Settings** → **Variables and
   Secrets** → add (as **Secret / Encrypted**):
   - `TELEGRAM_BOT_TOKEN` = your token
   - `TELEGRAM_CHAT_ID` = `-5487066468`
   - `TG_WEBHOOK_SECRET` = (the value in your `.env` — I'll wire this up)
   - `GITHUB_TOKEN` = the fine-grained token from step 1
   - `GITHUB_REPO` = `<YOUR_USERNAME>/bcn-rental-bot`

4. Copy the Worker's URL (looks like
   `https://buscpiso-trigger.<you>.workers.dev`) and give it to me — I'll point
   Telegram's webhook at it and re-register the `/sweep /status /help` menu.
   Then typing `/sweep` in the group runs a cloud sweep on demand, from either
   phone, Mac off.
