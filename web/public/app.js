const fmt = (v, d = 2) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));

async function load() {
  const status = document.getElementById("status");
  let data;
  try {
    const res = await fetch("/api/data");
    data = await res.json();
    if (data.error) throw new Error(data.error);
  } catch (e) {
    status.textContent = "Data temporarily unavailable.";
    return;
  }
  status.textContent =
    `Last screen: ${data.last_screen_date ?? "—"} · Last run: ${data.last_run_date ?? "—"} · ${data.today}`;

  const trades = document.getElementById("trades");
  if (!data.trades.length) {
    trades.innerHTML = `<p class="empty">No trades identified today.</p>`;
  } else {
    trades.innerHTML = data.trades.map((t) => {
      const p = t.payload || {};
      return `<div class="card">
        <span class="sym">${t.symbol}</span>
        <span class="score">${fmt(p.score, 0)}/100</span>
        <div>Spot ${fmt(p.spot)} · Put wall ${fmt(p.put_wall)}</div>
        <div>${p.suggested ?? ""}</div>
        <div class="empty">${p.notes ?? ""}</div>
      </div>`;
    }).join("");
  }

  const wl = document.getElementById("watchlist");
  wl.innerHTML = data.watchlist.length ? `<table>
    <tr><th>Symbol</th><th>Score</th><th>Spot</th><th>Put wall</th><th>Regime</th><th>IV rank</th><th>VRP</th><th>Sector</th></tr>
    ${data.watchlist.map((r) => `<tr>
      <td>${r.symbol}</td><td>${fmt(r.last_score, 0)}</td><td>${fmt(r.spot)}</td>
      <td>${fmt(r.put_wall)}</td>
      <td class="${r.regime === "positive" ? "pos" : r.regime === "negative" ? "neg" : ""}">${r.regime ?? "—"}</td>
      <td>${fmt(r.iv_rank, 0)}</td><td>${fmt(r.vrp)}</td><td>${r.sector ?? "—"}</td>
    </tr>`).join("")}
  </table>` : `<p class="empty">Watchlist is empty — run the screen to seed it.</p>`;

  const recent = document.getElementById("recent");
  recent.innerHTML = data.recent.length ? `<table>
    <tr><th>Date</th><th>Symbol</th><th>Score</th><th>Suggested</th></tr>
    ${data.recent.map((r) => `<tr>
      <td>${r.date}</td><td>${r.symbol}</td><td>${fmt(r.payload?.score, 0)}</td>
      <td>${r.payload?.suggested ?? ""}</td></tr>`).join("")}
  </table>` : `<p class="empty">No recent trades.</p>`;
}

load();
