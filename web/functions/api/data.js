import { createClient } from "@libsql/client/web";

function etTodayISO() {
  // YYYY-MM-DD in America/New_York (matches how the jobs store dates).
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const get = (t) => parts.find((p) => p.type === t).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export async function onRequest(context) {
  const { env } = context;
  const client = createClient({
    url: env.TURSO_DATABASE_URL,
    authToken: env.TURSO_READONLY_TOKEN,
  });

  try {
    const today = etTodayISO();

    const watchlist = await client.execute(
      `SELECT w.symbol, w.last_score, w.date_added, pw.sector,
              g.spot, g.put_wall, g.call_wall, g.regime,
              v.iv_rank, v.vrp
         FROM watchlist w
         LEFT JOIN primary_watchlist pw ON pw.symbol = w.symbol
         LEFT JOIN gex_snapshots g
                ON g.symbol = w.symbol
               AND g.date = (SELECT MAX(date) FROM gex_snapshots WHERE symbol = w.symbol)
         LEFT JOIN vol_stats v
                ON v.symbol = w.symbol
               AND v.date = (SELECT MAX(date) FROM vol_stats WHERE symbol = w.symbol)
        WHERE w.status = 'active'
        ORDER BY w.last_score DESC NULLS LAST, w.symbol`
    );

    const trades = await client.execute({
      sql: `SELECT symbol, type, payload_json, sent_at
              FROM alerts WHERE date = ? ORDER BY id DESC`,
      args: [today],
    });

    const recent = await client.execute({
      sql: `SELECT date, symbol, type, payload_json
              FROM alerts WHERE date < ? ORDER BY date DESC, id DESC LIMIT 25`,
      args: [today],
    });

    const lastScreen = await client.execute(
      `SELECT value FROM app_state WHERE key = 'last_screen_date'`
    );
    const lastGex = await client.execute(
      `SELECT MAX(date) AS d FROM gex_snapshots`
    );

    const body = {
      today,
      watchlist: watchlist.rows,
      trades: trades.rows.map((r) => ({ ...r, payload: JSON.parse(r.payload_json || "{}") })),
      recent: recent.rows.map((r) => ({ ...r, payload: JSON.parse(r.payload_json || "{}") })),
      last_screen_date: lastScreen.rows[0]?.value ?? null,
      last_run_date: lastGex.rows[0]?.d ?? null,
    };
    return new Response(JSON.stringify(body), {
      headers: { "content-type": "application/json", "cache-control": "public, max-age=300" },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: "data temporarily unavailable" }), {
      status: 502, headers: { "content-type": "application/json" },
    });
  }
}
