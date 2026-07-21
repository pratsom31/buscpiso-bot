// Telegram -> GitHub Actions trigger for the BCN rental bot (Phase 2).
//
// Deploy as a free Cloudflare Worker. It receives Telegram commands and:
//   /sweep     -> triggers the GitHub Actions sweep (repository_dispatch)
//   /status    -> reads state.cloud.json from the repo and reports counts
//   /help      -> what the bot does
//
// Worker secrets to set (Settings -> Variables -> Encrypt):
//   TELEGRAM_BOT_TOKEN   the bot token
//   TELEGRAM_CHAT_ID     your group id (-5487066468) — commands only obeyed here
//   TG_WEBHOOK_SECRET    the shared secret (already in your .env)
//   GITHUB_TOKEN         fine-grained PAT, repo scoped, Actions: read+write
//   GITHUB_REPO          "yourname/bcn-rental-bot"

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");

    // reject anything not carrying Telegram's secret header
    if (request.headers.get("x-telegram-bot-api-secret-token") !== env.TG_WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    const update = await request.json().catch(() => ({}));
    const msg = update.message || update.edited_message || {};
    const chat = msg.chat && msg.chat.id;
    const text = (msg.text || "").trim();

    // only obey commands from the configured group
    if (!chat || String(chat) !== String(env.TELEGRAM_CHAT_ID) || !text.startsWith("/")) {
      return new Response("ignored");
    }
    const cmd = text.split(/\s+/)[0].split("@")[0].toLowerCase();

    if (cmd === "/sweep") {
      // workflow_dispatch endpoint — pairs with the token's Actions:write
      const r = await fetch(
        `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/sweep.yml/dispatches`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "User-Agent": "bcn-rental-bot",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main" }),
      });
      await send(env, chat, r.ok
        ? "🔍 Sweep triggered — new agency/portal listings will arrive in ~2–3 min."
        : `⚠️ Could not trigger the sweep (GitHub ${r.status}). Check the Worker's GITHUB_TOKEN/GITHUB_REPO.`);
    } else if (cmd === "/status") {
      let out = "📊 Could not read cloud memory.";
      try {
        // public repo: the raw CDN needs no auth (cache-bust so it's fresh)
        const raw = await fetch(
          `https://raw.githubusercontent.com/${env.GITHUB_REPO}/main/state.cloud.json?t=${Date.now()}`,
          { headers: { "User-Agent": "bcn-rental-bot" } });
        const s = await raw.json();
        out = `📊 <b>Cloud status</b>\nQueued to send: ${(s.pending || []).length}\n` +
              `Listings remembered: ${Object.keys(s.seen || {}).length}\n` +
              `Cloud runs every 2h · type /sweep to run now`;
      } catch (e) {}
      await send(env, chat, out);
    } else if (cmd === "/help") {
      await send(env, chat,
        "🏠 I watch Barcelona agencies + Habitaclia + Pisos every 2h and post new " +
        "long-term flats (≤1000€, ≥30m², studio/1-bed) here, expired/seasonal filtered.\n" +
        "/sweep — run a cloud check now\n/status — queue & memory\n" +
        "(Idealista + Fotocasa are swept from the Mac separately.)");
    }
    return new Response("ok");
  },
};

async function send(env, chat, textHtml) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chat, text: textHtml, parse_mode: "HTML" }),
  });
}
