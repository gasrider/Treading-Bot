/* Trading-Bot Dashboard – Single-Page-App ohne Build-Schritt.
 * Daten kommen von der JSON-API des Dashboards (tradingbot/dashboard.py).
 * Charts: TradingView Lightweight Charts (Apache-2.0, lokal unter /static/vendor).
 */
"use strict";

const LWC = window.LightweightCharts;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const VIEWS = [
  { id: "overview", label: "Übersicht", icon: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" },
  { id: "markets", label: "Märkte", icon: "M3 12h4l3-8 4 16 3-8h4", stroke: true },
  { id: "chart", label: "Chart", icon: "M3 3v18h18M7 15l4-4 3 3 5-6" , stroke: true },
  { id: "ai", label: "KI", icon: "M9 3a3 3 0 0 0-3 3v.5A3 3 0 0 0 4 12a3 3 0 0 0 2 5.5V18a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM15 3a3 3 0 0 1 3 3v.5a3 3 0 0 1 2 5.5 3 3 0 0 1-2 5.5V18a3 3 0 0 1-6 0", stroke: true },
  { id: "trades", label: "Trades", icon: "M4 6h16M4 12h16M4 18h10", stroke: true },
  { id: "stats", label: "Statistik", icon: "M5 20V10M12 20V4M19 20v-7", stroke: true },
  { id: "settings", label: "Einstellungen", icon: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z", stroke: true },
  { id: "log", label: "Protokoll", icon: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01", stroke: true },
];
const ICONS = {
  pause: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>',
  play: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M7 5l12 7-12 7z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  theme: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>',
  shield: '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M12 2l8 3v6c0 5-3.4 9.4-8 11-4.6-1.6-8-6-8-11V5z"/></svg>',
};
const TF_LABEL = { "15m": "15 Min", "30m": "30 Min", "1h": "1 Std", "4h": "4 Std", "1d": "1 Tag" };
const PRESETS = [
  { name: "Vorsichtig", desc: "4-Std-Kerzen, 0,5 % Risiko, enge Notbremse – wenige, ruhige Trades.",
    timeframe: "4h", strategy: { stop_atr: 2.5, take_profit_atr: 5, trailing_atr: 3 },
    risk: { risk_per_trade: 0.005, max_position_pct: 0.2, max_open_positions: 2, max_daily_loss: 0.02, max_drawdown: 0.1 } },
  { name: "Ausgewogen", desc: "Standard: 1-Std-Kerzen, 1 % Risiko pro Trade, Notbremse bei −15 %.",
    timeframe: "1h", strategy: { stop_atr: 2, take_profit_atr: 4, trailing_atr: 2.5 },
    risk: { risk_per_trade: 0.01, max_position_pct: 0.3, max_open_positions: 2, max_daily_loss: 0.03, max_drawdown: 0.15 } },
  { name: "Offensiv", desc: "2 % Risiko, größere Positionen, mehr Paare gleichzeitig – höhere Schwankungen.",
    timeframe: "1h", strategy: { stop_atr: 1.5, take_profit_atr: 3, trailing_atr: 2 },
    risk: { risk_per_trade: 0.02, max_position_pct: 0.4, max_open_positions: 3, max_daily_loss: 0.05, max_drawdown: 0.25 } },
];

const S = {
  status: null, view: "overview", quote: "EUR",
  chart: { symbol: null, tf: null, toggles: { ema: true, rsi: true, markers: true }, data: null, objs: null, firstLoad: true },
  trades: null, tradeFilter: "", settings: null, draft: null, dirty: false, marketClass: "all", marketQuery: "",
  eventFilter: "all", overviewChart: null, statsCharts: null, timers: {},
};

// ------------------------------------------------------------ Helfer
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (d) => new Intl.NumberFormat("de-AT", { minimumFractionDigits: d, maximumFractionDigits: d });
const cur = () => (S.quote === "EUR" ? "€" : S.quote);
const ccy = (c) => (c === "EUR" ? "€" : c === "USD" ? "$" : c || "");
const money = (v, signed = false) => v == null ? "–" : (signed && v > 0 ? "+" : "") + nf(2).format(v) + " " + cur();
const pct = (v, signed = true, d = 2) => v == null ? "–" : (signed && v > 0 ? "+" : "") + nf(d).format(v) + " %";
const priceDigits = (v) => { const a = Math.abs(v); return a >= 100 ? 2 : a >= 1 ? 3 : a >= 0.01 ? 5 : 8; };
const price = (v) => v == null ? "–" : nf(priceDigits(v)).format(v);
const qty = (v) => v == null ? "–" : nf(6).format(v);
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const localTime = (iso, withDate = true) => !iso ? "–" : new Date(iso).toLocaleString("de-AT",
  withDate ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
const toUnix = (iso) => Math.floor(new Date(iso).getTime() / 1000);
function relTime(iso) {
  if (!iso) return "–";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.floor(s / 60)} Min.`;
  if (s < 86400) return `vor ${Math.floor(s / 3600)} Std.`;
  return `vor ${Math.floor(s / 86400)} Tagen`;
}
function shortRel(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "jetzt";
  if (s < 3600) return `${Math.floor(s / 60)} Min.`;
  if (s < 86400) return `${Math.floor(s / 3600)} Std.`;
  return `${Math.floor(s / 86400)} T.`;
}
function duration(hours) {
  if (hours == null) return "–";
  if (hours < 1) return `${Math.round(hours * 60)} Min.`;
  if (hours < 48) return `${nf(1).format(hours)} Std.`;
  return `${nf(1).format(hours / 24)} Tage`;
}
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* privat/blockiert */ } },
};
function icon(v) {
  const attrs = v.stroke ? 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"' : 'fill="currentColor"';
  return `<svg viewBox="0 0 24 24" ${attrs} aria-hidden="true"><path d="${v.icon}"/></svg>`;
}

// ------------------------------------------------------------ API
class AuthError extends Error {}
async function api(path, { method = "GET", body, session = true } = {}) {
  const res = await fetch(path, {
    method, credentials: "same-origin",
    headers: { "X-Requested-With": "tradingbot", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && session) { showLogin(); throw new AuthError("Bitte anmelden"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Fehler ${res.status}`);
  return data;
}

function toast(text, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = text;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 5000);
}

function confirmDialog(title, text, okLabel, danger = true) {
  const dlg = $("#confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  const ok = $("#confirm-ok");
  ok.textContent = okLabel;
  ok.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}

// ------------------------------------------------------------ Design (hell/dunkel)
function applyTheme(mode) {
  if (mode === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = mode;
  store.set("theme", mode);
  const label = { auto: "Design: Automatisch", dark: "Design: Dunkel", light: "Design: Hell" }[mode];
  $("#theme-btn").innerHTML = `${ICONS.theme}<span>${label}</span>`;
  $("#menu-theme-btn").innerHTML = ICONS.theme;
  $("#menu-theme-btn").title = label;
  $('meta[name="theme-color"]').content = css("--bg") || "#0b0e14";
  restyleCharts();
}
function cycleTheme() {
  const order = ["auto", "dark", "light"];
  applyTheme(order[(order.indexOf(store.get("theme", "auto")) + 1) % order.length]);
}

// ------------------------------------------------------------ Anmeldung
function showLogin() {
  $("#app").hidden = true;
  $("#login").hidden = false;
  Object.values(S.timers).forEach(clearInterval);
  S.timers = {};
  setTimeout(() => $('#login-form [name="password"]').focus(), 50);
}
async function login(ev) {
  ev.preventDefault();
  const f = ev.target;
  $("#login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", session: false, body: { user: f.user.value, password: f.password.value, remember: f.remember.checked } });
    f.password.value = "";
    startApp();
  } catch (e) {
    $("#login-error").textContent = e.message;
  }
}
async function logout() {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  showLogin();
}

// ------------------------------------------------------------ Navigation
function buildNav() {
  const item = (v) => `<button class="nav-item" data-view="${v.id}" type="button">${icon(v)}<span>${v.label}</span></button>`;
  $("#nav").innerHTML = VIEWS.map(item).join("");
  $("#tabbar").innerHTML = VIEWS.map(item).join("");
  $$(".nav-item").forEach((b) => b.addEventListener("click", () => go(b.dataset.view)));
}
function go(view) {
  if (!VIEWS.some((v) => v.id === view)) view = "overview";
  if (S.view === "settings" && view !== "settings" && S.dirty && !window.confirm("Ungespeicherte Änderungen verwerfen?")) return;
  S.view = view;
  if (location.hash !== `#/${view}`) history.replaceState(null, "", `#/${view}`);
  $$(".view").forEach((el) => el.classList.toggle("active", el.id === `view-${view}`));
  $$(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.view === view));
  document.title = `${VIEWS.find((v) => v.id === view).label} · Trading-Bot`;
  window.scrollTo({ top: 0 });
  if (view === "chart") loadChart();
  if (view === "trades" || view === "stats") loadTrades();
  if (view === "settings") loadSettings();
  if (view === "log") { renderLog(); loadLogs(); }
  if (view === "markets") renderMarkets();
  if (view === "ai") renderAI();
  if (view === "overview" && S.status) renderOverview();
}

// ------------------------------------------------------------ Status (alle 10 s)
async function refreshStatus() {
  try {
    const st = await api("/api/status");
    S.status = st;
    S.quote = st.bot.quote;
    renderTopbar();
    if (S.view === "overview") renderOverview();
    if (S.view === "log") renderLog();
    if (S.view === "markets" && document.activeElement?.id !== "market-search") renderMarkets();
    if (S.view === "ai") renderAI();
    if (S.view === "chart") renderChartSide();
    if (S.view === "stats" && S.trades) renderStats();
  } catch (e) {
    if (e instanceof AuthError) return;
    $("#banner").innerHTML = `<div class="banner banner-warn">Dashboard nicht erreichbar – neuer Versuch in Kürze. (${esc(e.message)})</div>`;
  }
}

function renderTopbar() {
  const b = S.status.bot;
  const pill = $("#status-pill");
  pill.className = `pill st-${b.status}`;
  $("#status-text").textContent = b.status_text;
  const badge = $("#mode-badge");
  badge.className = `badge badge-${b.mode}`;
  badge.textContent = b.mode === "live" ? "LIVE · echtes Geld" : "PAPER · virtuelles Geld";
  const pause = $("#pause-btn");
  pause.innerHTML = b.paused ? `${ICONS.play}<span>Fortsetzen</span>` : `${ICONS.pause}<span>Pause</span>`;
  pause.title = b.paused ? "Wieder Käufe erlauben" : "Keine neuen Käufe – offene Positionen und Stops bleiben aktiv";
  pause.disabled = b.halted;
  const stop = $("#stop-btn");
  const stopped = b.halted || b.stop_requested;
  stop.innerHTML = stopped ? `${ICONS.play}<span>Handel freigeben</span>` : `${ICONS.stop}<span>NOT-AUS</span>`;
  stop.className = `btn ${stopped ? "btn-success" : "btn-danger"}`;
  updateCountdown();

  const banners = [];
  if (b.status === "down") banners.push(["bad", `Der Bot gibt seit ${relTime(b.heartbeat).replace("vor ", "")} kein Lebenszeichen. Prüfe den Server (tradingbot status).`]);
  if (b.halted) banners.push(["bad", `Handel gestoppt: ${b.halt_reason}. Erst Ursache prüfen, dann „Handel freigeben“.`]);
  else if (b.stop_requested) banners.push(["bad", "Not-Aus angefordert – der Bot verkauft beim nächsten Durchlauf alles."]);
  if (b.paused && !b.halted) banners.push(["warn", "Pausiert: Der Bot kauft nichts Neues. Offene Positionen werden weiter mit Stop und Ziel überwacht."]);
  if (b.last_error && b.status !== "down") banners.push(["warn", `Letzter Durchlauf mit Fehler: ${b.last_error}`]);
  $("#banner").innerHTML = banners.map(([k, t]) => `<div class="banner banner-${k}">${esc(t)}</div>`).join("");
}

function updateCountdown() {
  const b = S.status?.bot;
  if (!b?.next_eval) { $("#next-eval").textContent = ""; return; }
  const s = Math.max(0, Math.round((new Date(b.next_eval) - Date.now()) / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const t = h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  $("#next-eval").textContent = `Nächste Auswertung in ${t} · ${TF_LABEL[b.timeframe] || b.timeframe}-Kerzen`;
}

async function control(action, symbol) {
  try {
    const r = await api("/api/control", { method: "POST", body: { action, symbol } });
    toast(r.message);
    refreshStatus();
  } catch (e) { if (!(e instanceof AuthError)) toast(e.message, "bad"); }
}
async function onStopButton() {
  const b = S.status?.bot;
  if (b && (b.halted || b.stop_requested)) {
    if (await confirmDialog("Handel wieder freigeben?", "Der Bot handelt ab dem nächsten Durchlauf wieder nach seinen Regeln. Prüfe vorher, warum er gestoppt wurde.", "Freigeben", false)) control("resume");
  } else if (await confirmDialog("NOT-AUS auslösen?", "Alle offenen Positionen werden sofort verkauft und der Bot kauft nichts mehr, bis du den Handel wieder freigibst.", "Alles verkaufen & stoppen")) {
    control("stop");
  }
}
async function onPauseButton() {
  if (S.status?.bot.paused) control("unpause");
  else if (await confirmDialog("Pause einlegen?", "Der Bot kauft nichts Neues. Offene Positionen bleiben bestehen und werden weiter mit Stop und Ziel überwacht.", "Pausieren", false)) control("pause");
}
async function closePosition(symbol) {
  if (await confirmDialog(`${symbol} jetzt verkaufen?`, "Die Position wird beim nächsten Durchlauf (spätestens in einer Minute) zum Marktpreis verkauft.", "Verkaufen")) control("close", symbol);
}

// ------------------------------------------------------------ Übersicht
function sparkSvg(values, tone) {
  if (!values || values.length < 2) return "";
  const w = 300, h = 44, min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1) * w).toFixed(1)},${(h - 3 - (v - min) / span * (h - 6)).toFixed(1)}`);
  const color = values[values.length - 1] >= values[0] ? "var(--ok)" : "var(--bad)";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

function rangeSvg(p) {
  // Kursleiste: Stop, Einstieg, aktueller Kurs und Ziel maßstabsgetreu
  if (!p.tp) return "";
  const lo = Math.min(p.stop, p.entry, p.price), hi = Math.max(p.tp, p.price);
  const x = (v) => +((v - lo) / (hi - lo || 1) * 100).toFixed(2);
  const e = x(p.entry), c = x(p.price);
  const fill = p.price >= p.entry ? "var(--ok)" : "var(--bad)";
  const tick = (v, color) => `<line x1="${x(v)}%" x2="${x(v)}%" y1="4" y2="22" stroke="${color}" stroke-width="2"/>`;
  return `<div class="range"><svg width="100%" height="26" aria-hidden="true">
      <rect x="0" y="10" width="100%" height="6" rx="3" fill="var(--border)"/>
      <rect x="${Math.min(e, c)}%" y="10" width="${Math.abs(c - e)}%" height="6" fill="${fill}"/>
      ${tick(p.stop, "var(--bad)")}${tick(p.entry, "var(--muted)")}${tick(p.tp, "var(--ok)")}
      <circle cx="${c}%" cy="13" r="6" fill="${fill}" stroke="var(--surface-2)" stroke-width="2"/>
    </svg>
    <div class="range-labels"><span><i class="tk tk-stop"></i>Stop ${price(p.stop)}</span><span><i class="tk tk-entry"></i>Einstieg ${price(p.entry)}</span>
      <span><i class="tk tk-now"></i>Kurs ${price(p.price)}</span><span><i class="tk tk-tp"></i>Ziel ${price(p.tp)}</span></div></div>`;
}

function kpi(label, value, sub = "", valueCls = "", hint = "") {
  return `<div class="kpi"><div class="kpi-label"><span>${esc(label)}</span>${hint ? `<span title="${esc(hint)}">ⓘ</span>` : ""}</div>
    <div class="kpi-value ${valueCls}">${value}</div><div class="kpi-sub">${sub}</div></div>`;
}

function regimeCard(st) {
  const r = st.regime;
  if (!r) return "";
  const tone = { risk_on: "ok", caution: "warn", risk_off: "bad" }[r.state] || "muted";
  return `<div class="card regime tone-${tone}"><div class="card-head"><h2>Marktlage</h2>
      <span class="badge regime-${esc(r.state)}">${esc(r.label)}</span></div>
    <div class="status-line tone-${tone}"><span class="dot"></span><span>${esc(r.text)}</span></div>
    <p class="muted small">Grundlage: S&amp;P 500 gegenüber seiner 200-Tage-Linie und Volatilitätsindex VIX. Bestimmt, mit wie viel Risiko neue Käufe eröffnet werden.</p></div>`;
}

function aiCard(st) {
  const ai = st.ai;
  if (!ai) return "";
  const b = ai.bilanz;
  let body;
  if (ai.mode === "off") body = '<p class="muted">KI ist ausgeschaltet (Einstellungen → KI).</p>';
  else if (!ai.available) body = '<p class="muted">KI aktiv, aber kein API-Schlüssel hinterlegt – es entscheiden die Regeln allein. Kostenlos einrichten: Seite „KI“.</p>';
  else {
    const last = ai.decisions[0];
    body = `${last ? `<div class="status-line ${last.decision === "reject" ? "tone-bad" : "tone-ok"}"><span class="dot"></span>
        <span><b>${esc(last.symbol)}</b>: ${last.decision === "reject" ? "Kauf verhindert" : last.decision === "approve" ? "genehmigt" : "nicht geprüft"} – ${esc(last.reason)}</span></div>` : '<p class="muted">Noch keine Kaufsignale geprüft.</p>'}
      <p class="small muted">KI-Bilanz: ${b.rejected} Käufe verhindert · davon wären ${b.would_lose} Verluste und ${b.would_win} Gewinne gewesen · ${ai.free ? `Google Gemini, kostenlos · ${ai.cost.calls} Aufrufe diesen Monat` : `Kosten diesen Monat ${nf(2).format(ai.cost.usd)} $ von ${nf(0).format(ai.budget_usd)} $`}</p>`;
  }
  return `<div class="card"><div class="card-head"><h2>KI-Risikomanager</h2><span class="badge badge-ai">${esc(ai.mode_label)}</span></div>
    ${body}<a href="#/ai" data-go="ai">Zur KI-Seite</a></div>`;
}

function renderOverview() {
  const st = S.status;
  if (!st) return;
  const a = st.account, s = st.stats;
  const vsBench = a.total_pct != null && a.benchmark_pct != null
    ? `Kaufen &amp; Halten: <span class="${cls(a.benchmark_pct)}">${pct(a.benchmark_pct)}</span>` : "seit Start";
  const kpis = [
    kpi("Kapital", money(a.equity), a.cash != null ? `davon frei ${money(a.cash)}` : ""),
    kpi("Heute", money(a.today_pnl, true), pct(a.today_pct), cls(a.today_pnl), "Veränderung seit Tagesbeginn (UTC)"),
    kpi("Gesamt-Rendite", pct(a.total_pct), vsBench, cls(a.total_pct),
      "Seit Start des Bots. Vergleich: alle Märkte beim Start gekauft und gehalten."),
    kpi("Offene Positionen", money(a.unrealized, true), `${st.positions.length} offen`, cls(a.unrealized), "Nicht realisierter Gewinn/Verlust in Euro"),
    kpi("Realisiert", money(a.realized, true), s.count ? `${s.count} Trades · ${nf(0).format(s.win_rate)} % Treffer` : "noch keine Trades", cls(a.realized)),
    kpi("Max. Drawdown", pct(a.max_dd_pct, false), `Notbremse bei −${nf(0).format(st.risk.max_drawdown * 100)} %`,
      "", "Größter Rückgang vom Höchststand"),
  ].join("");

  const ranked = st.markets.filter((m) => !m.pending && m.score != null).sort((x, y) => y.score - x.score);
  const top = ranked.slice(0, 6).map(marketCard).join("") || '<div class="empty">Die Rangliste erscheint, sobald der Bot alle Märkte ausgewertet hat (wenige Minuten).</div>';

  const positions = st.positions.map((p) => `<div class="position">
      <div class="position-top"><div><b>${esc(p.name !== p.symbol ? p.name : p.symbol)}</b> <span class="muted small">${esc(p.symbol)} · ${esc(st.asset_classes[p.asset_class] || "")} · seit ${localTime(p.opened_at)}</span>
        ${p.stop >= p.entry ? `<span class="chip chip-ok" title="Der Stop liegt über dem Einstieg">${ICONS.shield} Gewinn gesichert</span>` : ""}
        ${p.pending_exit ? `<span class="chip">Verkauf bei Marktöffnung (${esc(p.pending_exit)})</span>` : ""}</div>
        <div><span class="${cls(p.upnl)}"><b>${money(p.upnl, true)}</b> (${pct(p.upnl_pct)})</span>
        <button class="btn btn-secondary btn-sm" data-close="${esc(p.symbol)}" type="button">Verkaufen</button></div></div>
      <div class="position-meta muted">
        <span>Kurs<b>${price(p.price)} ${esc(ccy(p.currency))}</b></span><span>Menge<b>${qty(p.qty)}</b></span><span>Wert<b>${money(p.value)}</b></span>
        <span>Abstand Stop<b class="neg">${pct(p.stop_dist_pct)}</b></span>${p.tp_dist_pct != null ? `<span>Abstand Ziel<b class="pos">${pct(p.tp_dist_pct)}</b></span>` : ""}
      </div>${rangeSvg(p)}</div>`).join("") || '<div class="empty">Keine offenen Positionen – der Bot wartet auf ein Kaufsignal.</div>';

  const events = st.events.slice(0, 8).map(eventRow).join("") || '<div class="empty">Noch keine Ereignisse.</div>';
  const dataWarn = st.data_error ? `<div class="banner banner-warn">${esc(st.data_error)}</div>` : "";

  const view = $("#view-overview");
  view.innerHTML = `${dataWarn}<div class="kpis">${kpis}</div>
    <div class="grid-2">${regimeCard(st)}${aiCard(st)}</div>
    <div class="card spaced-top"><div class="card-head"><h2>Stärkste Märkte gerade</h2>
      <a href="#/markets" data-go="markets">Alle ${st.markets.length} Märkte</a></div><div class="markets">${top}</div></div>
    <div class="card"><div class="card-head"><h2>Offene Positionen</h2>
      <span class="muted small">Risiko pro Trade: ${money(st.account.risk_per_trade_eur)}</span></div><div class="positions">${positions}</div></div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h2>Kapitalverlauf</h2><span class="muted small">${a.since ? "seit " + localTime(a.since) : ""}</span></div>
        <div class="mini-chart" id="overview-equity"></div></div>
      <div class="card"><div class="card-head"><h2>Letzte Ereignisse</h2><a href="#/log" data-go="log">Alle anzeigen</a></div>
        <div class="events">${events}</div></div>
    </div>`;
  bindMarketClicks(view);
  $$("[data-close]", view).forEach((b) => b.addEventListener("click", () => closePosition(b.dataset.close)));
  $$("[data-go]", view).forEach((a2) => a2.addEventListener("click", (e) => { e.preventDefault(); go(a2.dataset.go); }));
  dropChart(S.overviewChart);
  S.overviewChart = equityChart($("#overview-equity"), st.equity_history);
}

function marketChips(m, st) {
  return [
    [m.uptrend, `Trend ✓ (EMA ${st.strategy.ema_trend})`, `kein Trend (EMA ${st.strategy.ema_trend})`],
    [m.fast_above_slow, `EMA ${st.strategy.ema_fast} > ${st.strategy.ema_slow}`, `EMA ${st.strategy.ema_fast} < ${st.strategy.ema_slow}`],
    [m.rsi_ok, `RSI ${m.rsi != null ? nf(0).format(m.rsi) : "–"}`, `RSI ${m.rsi != null ? nf(0).format(m.rsi) : "–"} hoch`],
  ].map(([ok, yes, no]) => `<span class="chip ${ok ? "chip-ok" : ""}">${ok ? "✓" : "✗"} ${esc(ok ? yes.replace(" ✓", "") : no)}</span>`).join("");
}

function marketCard(m) {
  const st = S.status;
  const span = m.change_span_hours >= 48 ? `${nf(0).format(m.change_span_hours / 24)} T` : `${nf(0).format(m.change_span_hours)} Std`;
  return `<div class="market" data-symbol="${esc(m.symbol)}" role="button" tabindex="0" title="Im Chart öffnen">
    <div class="market-top"><span><span class="market-sym">${esc(m.name)}</span> <span class="muted small">${esc(m.symbol)}</span></span>
      <span>${m.open === false ? '<span class="chip">geschlossen</span>' : ""}</span></div>
    <div class="market-top"><span class="muted small">${esc(st.asset_classes[m.asset_class] || "")} · Stärke ${m.score != null ? nf(2).format(m.score) : "–"}</span>
      <span><span class="market-price price">${price(m.price)}</span> <span class="muted small">${esc(ccy(m.currency))}</span> <span class="${cls(m.change_pct)} small">${pct(m.change_pct)}</span> <span class="muted small">${span}</span></span></div>
    ${sparkSvg(m.spark)}
    <div class="status-line tone-${esc(m.tone)}"><span class="dot"></span><span>${esc(m.text)}</span></div>
    <div class="chips">${marketChips(m, st)}</div></div>`;
}

function bindMarketClicks(root) {
  $$("[data-symbol]", root).forEach((el) => {
    const open = () => { S.chart.symbol = el.dataset.symbol; S.chart.firstLoad = true; go("chart"); };
    el.addEventListener("click", open);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  });
}

// ------------------------------------------------------------ Märkte (Scanner)
function renderMarkets() {
  const st = S.status;
  if (!st) return;
  const classes = [["all", "Alle"], ...Object.entries(st.asset_classes)];
  const q = (S.marketQuery || "").toLowerCase();
  const rows = st.markets
    .filter((m) => S.marketClass === "all" || m.asset_class === S.marketClass)
    .filter((m) => !q || m.symbol.toLowerCase().includes(q) || (m.name || "").toLowerCase().includes(q))
    .sort((x, y) => (y.score ?? -99) - (x.score ?? -99));
  const counts = Object.fromEntries(classes.map(([k]) => [k, k === "all" ? st.markets.length : st.markets.filter((m) => m.asset_class === k).length]));
  const top = st.scanner?.top_pct ?? 1;
  const body = rows.map((m) => {
    if (m.pending) {
      return `<tr data-symbol="${esc(m.symbol)}"><td><b>${esc(m.name)}</b><div class="muted small">${esc(m.symbol)}</div></td>
        <td class="hide-sm">${esc(st.asset_classes[m.asset_class] || "")}</td><td colspan="5" class="muted">${esc(m.error || "Wartet auf Daten …")}</td></tr>`;
    }
    const eligible = m.rank != null && m.rank >= 1 - top;
    return `<tr data-symbol="${esc(m.symbol)}" class="clickable">
      <td><b>${esc(m.name)}</b><div class="muted small">${esc(m.symbol)}</div></td>
      <td class="hide-sm">${esc(st.asset_classes[m.asset_class] || "")}</td>
      <td class="r">${price(m.price)} <span class="muted small">${esc(ccy(m.currency))}</span></td>
      <td class="r ${cls(m.change_pct)}">${pct(m.change_pct)}</td>
      <td class="r"><span class="rankbar" title="Platz im Ranking (100 % = stärkster Markt)"><svg width="60" height="8" viewBox="0 0 60 8" preserveAspectRatio="none" aria-hidden="true"><rect width="60" height="8" rx="4" fill="var(--border)"/><rect width="${Math.round((m.rank ?? 0) * 60)}" height="8" rx="4" fill="${eligible ? "var(--ok)" : "var(--muted)"}"/></svg></span>
        <span class="hide-sm">${m.score != null ? nf(2).format(m.score) : "–"}</span></td>
      <td class="hide-sm"><div class="chips nowrap" title="Trend über EMA ${st.strategy.ema_trend} · EMA ${st.strategy.ema_fast} über ${st.strategy.ema_slow} · RSI unter ${st.strategy.rsi_max_entry}">${[[m.uptrend, "Trend"], [m.fast_above_slow, "Kreuz"], [m.rsi_ok, `RSI ${m.rsi != null ? nf(0).format(m.rsi) : "–"}`]]
        .map(([ok, t]) => `<span class="chip ${ok ? "chip-ok" : ""}">${ok ? "✓" : "✗"} ${esc(t)}</span>`).join("")}</div></td>
      <td class="status-cell hide-sm tone-${esc(m.tone)}"><span class="dot"></span>${esc(m.entry_signal ? "Kaufsignal" : m.tone === "ok" ? "Position offen" : m.uptrend ? (m.fast_above_slow ? "Trend läuft" : "wartet auf Signal") : "kein Trend")}
        <div class="muted small">${m.open === false ? "Börse geschlossen" : "Börse offen"}</div></td></tr>`;
  }).join("");
  $("#view-markets").innerHTML = `
    <div class="card"><div class="card-head"><h2>Marktscanner</h2><span class="muted small">Sortiert nach Stärke · grün = unter den stärksten ${nf(0).format(top * 100)} % (nur dort wird gekauft)</span></div>
      <div class="toolbar"><div class="seg" id="class-filter">${classes.map(([k, l]) => `<button type="button" data-k="${k}" class="${k === S.marketClass ? "active" : ""}">${esc(l)} <span class="muted">${counts[k]}</span></button>`).join("")}</div>
        <input type="text" id="market-search" placeholder="Suchen (z. B. Gold, SAP, BTC)" value="${esc(S.marketQuery || "")}" autocomplete="off"></div>
      <div class="table-wrap"><table class="scanner"><thead><tr><th>Markt</th><th class="hide-sm">Klasse</th><th class="r">Kurs</th><th class="r">Veränd.</th>
        <th class="r">Stärke</th><th class="hide-sm">Bedingungen</th><th class="hide-sm">Status</th></tr></thead><tbody>${body || '<tr><td colspan="7" class="empty">Keine Treffer.</td></tr>'}</tbody></table></div>
      <p class="muted small">Stärke = Rendite der letzten 20/60/120 Kerzen im Verhältnis zur typischen Schwankung. Veränderung über die letzten ${nf(0).format((st.markets.find((m) => m.change_span_hours)?.change_span_hours || 96) / 24)} Tage. Klick öffnet den Chart.</p></div>
    <div class="card"><h2>Hinweis zu den Daten</h2><p class="muted small">Krypto-Kurse kommen live von ${esc(st.bot.exchange)}. Aktien, ETFs, Rohstoffe und Devisen kommen von Yahoo Finance (teils ~15 Minuten verzögert) und werden nur im Paper-Modus gehandelt. Gekauft wird nur, wenn die jeweilige Börse geöffnet ist.</p></div>`;
  $$("#class-filter button").forEach((b) => b.onclick = () => { S.marketClass = b.dataset.k; renderMarkets(); });
  const search = $("#market-search");
  search.oninput = () => { S.marketQuery = search.value; const pos = search.selectionStart; renderMarkets(); const s2 = $("#market-search"); s2.focus(); s2.setSelectionRange(pos, pos); };
  bindMarketClicks($("#view-markets"));
}

// ------------------------------------------------------------ KI
function mdLite(text) {
  // Minimaler, sicherer Markdown-Renderer: erst escapen, dann Überschriften, Listen, Fett
  const out = [];
  let list = false;
  for (const raw of esc(text).split("\n")) {
    const line = raw.trim();
    const item = line.match(/^[-*] (.*)/) || line.match(/^\d+\. (.*)/);
    if (item) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${item[1]}</li>`); continue; }
    if (list) { out.push("</ul>"); list = false; }
    const h = line.match(/^#{1,4}\s+(.*)/);
    if (h) out.push(`<h3 class="md-h">${h[1]}</h3>`);
    else if (line) out.push(`<p>${line}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
}

function renderAI() {
  const st = S.status;
  if (!st) return;
  const ai = st.ai, b = ai.bilanz;
  const setup = !ai.available ? `<div class="card"><h2>So aktivierst du die KI – kostenlos</h2><ol class="explain">
      <li>Auf <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer">aistudio.google.com/apikey</a> mit deinem Google-Konto anmelden und auf „API-Schlüssel erstellen“ klicken. Keine Kreditkarte nötig.</li>
      <li>Auf dem Server <code>tradingbot env</code> ausführen und den Schlüssel bei <code>GEMINI_API_KEY=</code> eintragen (Zeile ggf. selbst ergänzen), speichern.</li>
      <li>Fertig – der Bot nutzt ab dem nächsten Durchlauf Google Gemini. Im Gratis-Kontingent entstehen keine Kosten; ist das Tageslimit erreicht, entscheiden einfach die Regeln.</li></ol>
      <p class="muted small">Alternativ (kostenpflichtig): Claude von Anthropic mit <code>ANTHROPIC_API_KEY</code>.</p></div>` : "";
  const brief = ai.brief ? `<div class="brief">${mdLite(ai.brief.text)}</div>
      ${ai.brief.sources?.length ? `<details><summary class="muted small">${ai.brief.sources.length} Quellen</summary><ul class="sources">${ai.brief.sources.map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title || s.url)}</a></li>`).join("")}</ul></details>` : ""}
      <p class="muted small">Erstellt ${localTime(ai.brief.t)} · ${esc(ai.brief.model)} · ${ai.brief.cost_usd ? `ca. ${nf(2).format(ai.brief.cost_usd)} $` : "kostenlos"}</p>`
    : `<p class="muted">${esc(ai.brief_error || "Noch keine Analyse – sie entsteht einmal täglich (ab 06:00 UTC), sobald die KI aktiv ist.")}</p>`;
  const decisions = ai.decisions.map((d) => `<tr><td>${localTime(d.t)}</td><td><b>${esc(d.symbol)}</b></td>
      <td><span class="chip ${d.decision === "approve" ? "chip-ok" : d.decision === "reject" ? "chip-bad" : ""}">${d.decision === "approve" ? "genehmigt" : d.decision === "reject" ? (d.enforced ? "verhindert" : "abgelehnt (Hinweis)") : "Fehler"}</span></td>
      <td>${esc(d.confidence || "")}</td><td class="wrap">${esc(d.reason)}${d.risks?.length ? `<div class="muted small">Risiken: ${d.risks.map(esc).join(" · ")}</div>` : ""}</td></tr>`).join("");
  const shadows = ai.shadow.map((s) => `<tr><td>${localTime(s.t)}</td><td><b>${esc(s.symbol)}</b></td><td class="r">${price(s.entry)}</td>
      <td><span class="chip ${s.status === "wäre Verlust" ? "chip-ok" : s.status === "wäre Gewinn" ? "chip-bad" : ""}">${esc(s.status)}</span></td>
      <td class="r ${cls(s.result_pct)}">${pct(s.result_pct)}</td><td class="wrap muted small">${esc(s.reason)}</td></tr>`).join("");
  $("#view-ai").innerHTML = `
    <div class="kpis">
      ${kpi("Modus", esc(ai.mode_label), ai.available ? `${ai.provider === "gemini" ? "Google Gemini" : "Claude"} · ${esc(ai.model)}` : "kein API-Schlüssel – Regeln entscheiden")}
      ${ai.free || !ai.available ? kpi("Kosten", "0,00 $", `kostenlos (Gratis-Kontingent) · ${ai.cost.calls} Aufrufe diesen Monat`)
        : kpi("Kosten diesen Monat", `${nf(2).format(ai.cost.usd)} $`, `Budget ${nf(0).format(ai.budget_usd)} $ · ${ai.cost.calls} Aufrufe`)}
      ${kpi("Käufe verhindert", String(b.rejected), `${b.open} noch offen`)}
      ${kpi("Davon wären Verluste", String(b.would_lose), `und ${b.would_win} Gewinne`, b.would_lose >= b.would_win ? "pos" : "neg", "So wären die verhinderten Käufe mit denselben Stop-/Ziel-Regeln ausgegangen")}
      ${kpi("Vermieden (Summe)", pct(b.avoided_pct), "positiv = KI hat Verluste vermieden", cls(b.avoided_pct))}
    </div>
    ${setup}
    <div class="card"><div class="card-head"><h2>Marktanalyse des Tages</h2></div>${brief}</div>
    <div class="card"><div class="card-head"><h2>Geprüfte Kaufsignale</h2><span class="muted small">Die KI kann Käufe nur verhindern – nie auslösen, nie Stops ändern.</span></div>
      <div class="table-wrap"><table><thead><tr><th>Zeit</th><th>Markt</th><th>Entscheidung</th><th>Sicherheit</th><th>Begründung</th></tr></thead>
      <tbody>${decisions || '<tr><td colspan="5" class="empty">Noch keine Kaufsignale geprüft.</td></tr>'}</tbody></table></div></div>
    <div class="card"><div class="card-head"><h2>KI-Bilanz: Was wäre aus verhinderten Käufen geworden?</h2></div>
      <p class="muted small">Jeder verhinderte Kauf wird im Hintergrund mit denselben Stop- und Ziel-Regeln weiterverfolgt (max. 14 Tage). „Wäre Verlust“ heißt: Die KI lag richtig.</p>
      <div class="table-wrap"><table><thead><tr><th>Zeit</th><th>Markt</th><th class="r">Kurs</th><th>Ergebnis</th><th class="r">%</th><th>KI-Begründung</th></tr></thead>
      <tbody>${shadows || '<tr><td colspan="6" class="empty">Noch keine verhinderten Käufe.</td></tr>'}</tbody></table></div></div>`;
}

function eventRow(ev) {
  return `<div class="event event-${esc(ev.kind)}"><time datetime="${esc(ev.t)}" title="${esc(localTime(ev.t))}">${esc(shortRel(ev.t))}</time><span>${esc(ev.text)}</span></div>`;
}

// ------------------------------------------------------------ Charts (gemeinsam)
const charts = new Set();
function chartOptions(extra = {}) {
  return {
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: css("--muted"), fontFamily: getComputedStyle(document.body).fontFamily, attributionLogo: false },
    grid: { vertLines: { color: css("--border") }, horzLines: { color: css("--border") } },
    rightPriceScale: { borderColor: css("--border") },
    timeScale: { borderColor: css("--border"), timeVisible: true, secondsVisible: false,
      tickMarkFormatter: (t, type) => {
        const d = new Date(t * 1000);
        if (type === 0) return String(d.getFullYear());
        if (type === 1) return d.toLocaleDateString("de-AT", { month: "short" });
        if (type === 2) return d.toLocaleDateString("de-AT", { day: "2-digit", month: "2-digit" });
        return d.toLocaleTimeString("de-AT", { hour: "2-digit", minute: "2-digit" });
      } },
    localization: { locale: "de-AT", priceFormatter: (p) => price(p), timeFormatter: (t) => new Date(t * 1000).toLocaleString("de-AT", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) },
    crosshair: { mode: LWC.CrosshairMode.Normal },
    handleScroll: true, handleScale: true,
    ...extra,
  };
}
function makeChart(el, extra) {
  const chart = LWC.createChart(el, chartOptions(extra));
  charts.add(chart);
  return chart;
}
function dropChart(chart) {
  if (!chart) return;
  charts.delete(chart);
  chart.remove();
}
function restyleCharts() {
  if (!LWC) return;
  charts.forEach((c) => c.applyOptions({
    layout: { textColor: css("--muted") }, grid: { vertLines: { color: css("--border") }, horzLines: { color: css("--border") } },
    rightPriceScale: { borderColor: css("--border") }, timeScale: { borderColor: css("--border") },
  }));
  if (S.chart.objs) {
    const o = S.chart.objs;
    o.fast.applyOptions({ color: css("--ema-fast") });
    o.slow.applyOptions({ color: css("--ema-slow") });
    o.trend.applyOptions({ color: css("--ema-trend") });
  }
  if (S.status && S.view === "overview") renderOverview();
}
function equityChart(el, history) {
  if (!el) return null;
  if (!history || history.length < 2) {
    el.innerHTML = '<div class="empty">Der Kapitalverlauf erscheint, sobald der Bot ein paar Stunden läuft.</div>';
    return null;
  }
  const chart = makeChart(el, { rightPriceScale: { borderColor: css("--border"), scaleMargins: { top: 0.15, bottom: 0.05 } } });
  const up = history[history.length - 1][1] >= history[0][1];
  const color = up ? css("--ok") : css("--bad");
  const series = chart.addAreaSeries({ lineColor: color, topColor: color + "55", bottomColor: color + "05", lineWidth: 2,
    priceFormat: { type: "price", precision: 2, minMove: 0.01 } });
  series.setData(history.map(([t, v]) => ({ time: toUnix(t), value: v })));
  chart.timeScale().fitContent();
  return chart;
}

// ------------------------------------------------------------ Chart-Ansicht
function chartToolbar() {
  const st = S.status;
  const markets = st ? st.markets : [];
  const symbols = markets.map((m) => m.symbol);
  if (!S.chart.symbol || !symbols.includes(S.chart.symbol)) S.chart.symbol = (markets.find((m) => !m.pending) || markets[0])?.symbol;
  if (!S.chart.tf) S.chart.tf = st?.bot.timeframe || "1h";
  const groups = Object.entries(st?.asset_classes || {}).map(([k, label]) => {
    const items = markets.filter((m) => m.asset_class === k);
    return items.length ? `<optgroup label="${esc(label)}">${items.map((m) => `<option value="${esc(m.symbol)}" ${m.symbol === S.chart.symbol ? "selected" : ""}>${esc(m.name)} (${esc(m.symbol)})</option>`).join("")}</optgroup>` : "";
  }).join("");
  $("#chart-symbols").innerHTML = `<select id="chart-symbol" aria-label="Markt wählen">${groups}</select>`;
  $("#chart-symbol").onchange = (e) => { S.chart.symbol = e.target.value; S.chart.firstLoad = true; loadChart(); };
  $("#chart-tfs").innerHTML = Object.entries(TF_LABEL).map(([tf, l]) => `<button type="button" data-tf="${tf}" class="${tf === S.chart.tf ? "active" : ""}" title="${l}">${tf}</button>`).join("");
  const t = S.chart.toggles;
  $("#chart-toggles").innerHTML = [["ema", "EMAs"], ["markers", "Trades"], ["rsi", "RSI"]]
    .map(([k, l]) => `<button type="button" data-t="${k}" class="${t[k] ? "active" : ""}">${l}</button>`).join("");
  $$("#chart-tfs button").forEach((b) => b.onclick = () => { S.chart.tf = b.dataset.tf; S.chart.firstLoad = true; loadChart(); });
  $$("#chart-toggles button").forEach((b) => b.onclick = () => { t[b.dataset.t] = !t[b.dataset.t]; chartToolbar(); drawChart(); });
}

async function loadChart() {
  if (!S.status) await refreshStatus();
  chartToolbar();
  renderChartSide();
  if (!S.chart.symbol) return;
  const { symbol, tf } = S.chart;
  try {
    const data = await api(`/api/chart?symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(tf)}`);
    if (symbol !== S.chart.symbol || tf !== S.chart.tf) return; // inzwischen umgeschaltet
    S.chart.data = data;
    drawChart();
  } catch (e) {
    if (!(e instanceof AuthError)) $("#chart-foot").textContent = e.message;
  }
}

function drawChart() {
  const d = S.chart.data;
  if (!d || !LWC) return;
  const t = S.chart.toggles;
  let o = S.chart.objs;
  if (!o) {
    // TradingView-Logo (Lizenzhinweis) im Hauptchart
    const main = makeChart($("#chart-main"), { layout: { background: { type: "solid", color: "transparent" }, textColor: css("--muted"), fontFamily: getComputedStyle(document.body).fontFamily, attributionLogo: true } });
    const rsi = makeChart($("#chart-rsi"), { timeScale: { visible: false, borderColor: css("--border") }, rightPriceScale: { borderColor: css("--border"), scaleMargins: { top: 0.1, bottom: 0.1 } } });
    o = S.chart.objs = {
      main, rsi,
      candles: main.addCandlestickSeries({ upColor: css("--ok"), downColor: css("--bad"), borderVisible: false, wickUpColor: css("--ok"), wickDownColor: css("--bad") }),
      trend: main.addLineSeries({ color: css("--ema-trend"), lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }),
      slow: main.addLineSeries({ color: css("--ema-slow"), lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }),
      fast: main.addLineSeries({ color: css("--ema-fast"), lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }),
      rsiLine: rsi.addLineSeries({ color: css("--accent"), lineWidth: 2, priceLineVisible: false, lastValueVisible: true }),
      levelLines: [], rsiLines: [],
    };
    // Zeitachsen von Kurs- und RSI-Chart synchron halten
    let syncing = false;
    const sync = (from, to) => from.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (syncing || !r) return;
      syncing = true; to.timeScale().setVisibleLogicalRange(r); syncing = false;
    });
    sync(main, rsi); sync(rsi, main);
    main.subscribeCrosshairMove((param) => legend(param.time ? param : null));
  }
  const digits = priceDigits(d.candles.at(-1)?.close ?? 1);
  const pf = { type: "price", precision: digits, minMove: 1 / 10 ** digits };
  o.candles.applyOptions({ priceFormat: pf });
  o.candles.setData(d.candles);
  for (const [k, s] of [["ema_fast", o.fast], ["ema_slow", o.slow], ["ema_trend", o.trend]]) {
    s.applyOptions({ visible: t.ema, priceFormat: pf });
    s.setData(d[k]);
  }
  o.candles.setMarkers(t.markers ? d.markers : []);
  o.levelLines.forEach((l) => o.candles.removePriceLine(l));
  o.levelLines = [];
  if (d.levels) {
    const add = (p, color, title) => p && o.levelLines.push(o.candles.createPriceLine({ price: p, color, lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: true, title }));
    add(d.levels.entry, css("--accent"), "Einstieg");
    add(d.levels.stop, css("--bad"), "Stop");
    add(d.levels.tp, css("--ok"), "Ziel");
  }
  o.rsiLine.setData(d.rsi);
  o.rsiLines.forEach((l) => o.rsiLine.removePriceLine(l));
  o.rsiLines = [
    o.rsiLine.createPriceLine({ price: d.params.rsi_max_entry, color: css("--warn"), lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: false, title: "Kauf-Grenze" }),
    o.rsiLine.createPriceLine({ price: 30, color: css("--muted"), lineWidth: 1, lineStyle: LWC.LineStyle.Dotted, axisLabelVisible: false }),
  ];
  $("#chart-rsi").hidden = !t.rsi;
  $(".chart-sub-label").hidden = !t.rsi;
  if (S.chart.firstLoad) {
    const n = d.candles.length;
    o.main.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 160), to: n + 3 });
    S.chart.firstLoad = false;
  }
  legend(null);
  const src = d.source === "exchange" ? `Live-Daten von ${esc(S.status?.bot.exchange || "der Börse")}`
    : d.source === "yahoo" ? "Kursdaten von Yahoo Finance (teils ~15 Min. verzögert)" : "Momentaufnahme des Bots (Datenquelle gerade nicht erreichbar)";
  $("#chart-foot").innerHTML = `<span>${src} · aktualisiert ${localTime(new Date().toISOString(), false)} · Mausrad/Ziehen zum Zoomen</span>
    ${d.symbol.includes("/") ? `<a href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent((S.status?.bot.exchange || "").toUpperCase() + ":" + d.symbol.replace("/", ""))}" target="_blank" rel="noopener noreferrer">Auf TradingView öffnen ↗</a>`
      : `<a href="https://finance.yahoo.com/quote/${encodeURIComponent(d.symbol)}" target="_blank" rel="noopener noreferrer">Auf Yahoo Finance öffnen ↗</a>`}`;
}

function legend(param) {
  const d = S.chart.data, o = S.chart.objs;
  if (!d || !o) return;
  const p = d.params;
  let bar, vals = {};
  if (param) {
    bar = param.seriesData.get(o.candles);
    vals = { fast: param.seriesData.get(o.fast)?.value, slow: param.seriesData.get(o.slow)?.value, trend: param.seriesData.get(o.trend)?.value };
  } else {
    bar = d.candles.at(-1);
    vals = { fast: d.ema_fast.at(-1)?.value, slow: d.ema_slow.at(-1)?.value, trend: d.ema_trend.at(-1)?.value };
  }
  if (!bar) return;
  const chg = bar.open ? (bar.close / bar.open - 1) * 100 : 0;
  $("#chart-legend").innerHTML = `<b>${esc(d.name || d.symbol)}</b> <span class="muted">${esc(ccy(d.currency))}</span>
    <span>O <b>${price(bar.open)}</b></span><span>H <b>${price(bar.high)}</b></span><span>T <b>${price(bar.low)}</b></span>
    <span>S <b>${price(bar.close)}</b> <span class="${cls(chg)}">${pct(chg)}</span></span>
    ${S.chart.toggles.ema ? `<span><span class="sw sw-fast"></span>EMA ${p.ema_fast} ${price(vals.fast)}</span>
    <span><span class="sw sw-slow"></span>EMA ${p.ema_slow} ${price(vals.slow)}</span>
    <span><span class="sw sw-trend"></span>EMA ${p.ema_trend} ${price(vals.trend)}</span>` : ""}`;
}

function renderChartSide() {
  const st = S.status;
  if (!st || !S.chart.symbol) return;
  const m = st.markets.find((x) => x.symbol === S.chart.symbol);
  const p = st.positions.find((x) => x.symbol === S.chart.symbol);
  const s = st.strategy;
  let html = `<div class="card-head"><h2>${esc(m?.name || S.chart.symbol)} – Bot-Sicht</h2><span class="muted small">${m?.updated ? "Stand " + relTime(m.updated) : ""}</span></div>`;
  if (m && !m.pending) {
    html += `<div class="status-line tone-${esc(m.tone)}"><span class="dot"></span><span>${esc(m.text)}</span></div>
      <div class="chips">${[[m.uptrend, `Kurs über EMA ${s.ema_trend}`], [m.fast_above_slow, `EMA ${s.ema_fast} über EMA ${s.ema_slow}`], [m.rsi_ok, `RSI ${m.rsi != null ? nf(1).format(m.rsi) : "–"} unter ${s.rsi_max_entry}`]]
        .map(([ok, t]) => `<span class="chip ${ok ? "chip-ok" : ""}">${ok ? "✓" : "✗"} ${esc(t)}</span>`).join("")}</div>`;
  } else {
    html += '<div class="empty">Noch keine Auswertung vom Bot.</div>';
  }
  if (p) {
    html += `<h3 class="spaced">Offene Position</h3><div class="position-meta muted"><span>Einstieg<b>${price(p.entry)}</b></span><span>Kurs<b>${price(p.price)}</b></span>
      <span>Ergebnis<b class="${cls(p.upnl)}">${money(p.upnl, true)}</b></span></div>${rangeSvg(p)}
      <button class="btn btn-secondary btn-sm" id="chart-close" type="button">Position verkaufen</button>`;
  }
  $("#chart-status").innerHTML = html;
  $("#chart-close")?.addEventListener("click", () => closePosition(p.symbol));
  $("#chart-explain").innerHTML = `<div class="card-head"><h2>So entscheidet der Bot</h2></div>
    <ol class="explain">
      <li><b>Trendfilter:</b> Nur kaufen, wenn der Kurs über der grauen EMA ${s.ema_trend} liegt.</li>
      <li><b>Signal:</b> Die orange EMA ${s.ema_fast} kreuzt die violette EMA ${s.ema_slow} nach oben.</li>
      <li><b>Nicht überkauft:</b> RSI unter ${s.rsi_max_entry} (unteres Fenster).</li>
      <li><b>Ausstieg:</b> Stop ${s.stop_atr}× ATR unter Einstieg${s.take_profit_atr ? `, Ziel ${s.take_profit_atr}× ATR darüber` : ""}${s.trailing_atr ? `, Stop wird ${s.trailing_atr}× ATR nachgezogen` : ""} – oder wenn die EMAs nach unten kreuzen.</li>
    </ol><p class="muted small">Entschieden wird nur beim Schluss einer Kerze. Die aktuelle Kerze im Chart bewegt sich noch.</p>`;
}

// ------------------------------------------------------------ Trades & Statistik
async function loadTrades() {
  try {
    S.trades = await api("/api/trades");
    if (S.view === "trades") renderTrades();
    if (S.view === "stats") renderStats();
  } catch (e) { if (!(e instanceof AuthError)) toast(e.message, "bad"); }
}

function statsGrid(s) {
  return `<div class="kpis">
    ${kpi("Trades", String(s.count), `${s.wins} Gewinner · ${s.losses} Verlierer`)}
    ${kpi("Trefferquote", s.win_rate != null ? nf(1).format(s.win_rate) + " %" : "–", "Bei Trendfolge sind 35–45 % normal")}
    ${kpi("Profit-Faktor", s.profit_factor != null ? nf(2).format(s.profit_factor) : "–", "Gewinne ÷ Verluste · über 1,3 ist gut", "", "Summe aller Gewinne geteilt durch Summe aller Verluste")}
    ${kpi("Ergebnis gesamt", money(s.total_pnl, true), `Ø pro Trade ${money(s.expectancy, true)}`, cls(s.total_pnl))}
    ${kpi("Ø Gewinn / Ø Verlust", `<span class="pos">${money(s.avg_win)}</span>`, `<span class="neg">${money(s.avg_loss)}</span>`)}
    ${kpi("Ø Haltedauer", duration(s.avg_hold_hours), `Bester ${money(s.best, true)} · Schlechtester ${money(s.worst, true)}`)}
  </div>`;
}

function renderTrades() {
  const d = S.trades;
  if (!d) return;
  const symbols = [...new Set(d.trades.map((t) => t.symbol))];
  const rows = d.trades.filter((t) => !S.tradeFilter || t.symbol === S.tradeFilter);
  const classes = S.status?.asset_classes || {};
  const body = rows.map((t) => `<tr><td>${esc(localTime(t.closed_at))}</td><td><b>${esc(t.symbol)}</b><div class="muted small">${esc(classes[t.asset_class || "crypto"] || "")}</div></td>
      <td class="r">${price(t.entry)} <span class="muted small">${esc(ccy(t.currency || S.quote))}</span></td><td class="r">${price(t.exit)}</td><td class="r">${qty(t.qty)}</td>
      <td class="r ${cls(t.pnl)}"><b>${money(t.pnl, true)}</b></td><td class="r ${cls(t.pnl)}">${pct((t.exit / t.entry - 1) * 100)}</td>
      <td>${esc(t.reason)}</td><td class="muted">${esc(duration((new Date(t.closed_at) - new Date(t.opened_at)) / 3.6e6))}</td></tr>`).join("");
  $("#view-trades").innerHTML = `${statsGrid(d.stats)}
    <div class="card"><div class="card-head"><h2>Alle abgeschlossenen Trades</h2>
      <div class="topbar-right"><select id="trade-filter" aria-label="Nach Paar filtern"><option value="">Alle Märkte</option>
        ${symbols.map((s) => `<option ${s === S.tradeFilter ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
        <a class="btn btn-secondary btn-sm" href="/api/trades.csv" download>⬇ CSV (Excel/Steuer)</a></div></div>
      <div class="table-wrap"><table><thead><tr><th>Geschlossen</th><th>Paar</th><th class="r">Kauf</th><th class="r">Verkauf</th><th class="r">Menge</th>
        <th class="r">Ergebnis</th><th class="r">%</th><th>Grund</th><th>Dauer</th></tr></thead>
      <tbody>${body || '<tr><td colspan="9" class="empty">Noch keine abgeschlossenen Trades. Der Bot handelt nur bei klaren Signalen – das kann Tage dauern.</td></tr>'}</tbody></table></div></div>`;
  $("#trade-filter").onchange = (e) => { S.tradeFilter = e.target.value; renderTrades(); };
}

function renderStats() {
  const d = S.trades, st = S.status;
  if (!d || !st) return;
  const s = d.stats;
  if (S.statsCharts) S.statsCharts.forEach(dropChart);
  const bySym = s.by_symbol.map((r) => `<tr><td><b>${esc(r.symbol)}</b></td><td class="r">${r.count}</td>
    <td class="r">${nf(0).format(r.win_rate)} %</td><td class="r ${cls(r.pnl)}">${money(r.pnl, true)}</td></tr>`).join("");
  $("#view-stats").innerHTML = `${statsGrid(s)}
    <div class="grid-2">
      <div class="card"><div class="card-head"><h2>Kapitalverlauf</h2><span class="muted small">Max. Drawdown ${pct(st.account.max_dd_pct, false)}</span></div><div class="mini-chart" id="stats-equity"></div></div>
      <div class="card"><div class="card-head"><h2>Ergebnis pro Tag</h2></div><div class="mini-chart" id="stats-daily"></div></div>
    </div>
    <div class="card"><div class="card-head"><h2>Nach Paar</h2></div><div class="table-wrap"><table>
      <thead><tr><th>Paar</th><th class="r">Trades</th><th class="r">Treffer</th><th class="r">Ergebnis</th></tr></thead>
      <tbody>${bySym || '<tr><td colspan="4" class="empty">Noch keine Daten.</td></tr>'}</tbody></table></div></div>
    <div class="card"><h2>Vergleich mit „Kaufen &amp; Halten“</h2>
      <p>Bot seit Start: <b class="${cls(st.account.total_pct)}">${pct(st.account.total_pct)}</b> · Alle Paare beim Start gekauft und gehalten:
      <b class="${cls(st.account.benchmark_pct)}">${pct(st.account.benchmark_pct)}</b></p>
      <p class="muted small">Ein Trendfolge-Bot soll vor allem große Verluste vermeiden. In starken Aufwärtsphasen liegt „Kaufen &amp; Halten“ oft vorne, in Abwärtsphasen der Bot.</p></div>`;
  const charts2 = [];
  const eq = equityChart($("#stats-equity"), st.equity_history);
  if (eq) charts2.push(eq);
  if (s.daily.length) {
    const c = makeChart($("#stats-daily"), { timeScale: { borderColor: css("--border"), timeVisible: false } });
    c.addHistogramSeries({ priceFormat: { type: "price", precision: 2, minMove: 0.01 } })
      .setData(s.daily.map((r) => ({ time: toUnix(r.date + "T00:00:00Z"), value: r.pnl, color: r.pnl >= 0 ? css("--ok") : css("--bad") })));
    c.timeScale().fitContent();
    charts2.push(c);
  } else {
    $("#stats-daily").innerHTML = '<div class="empty">Erscheint nach den ersten abgeschlossenen Trades.</div>';
  }
  S.statsCharts = charts2;
}

// ------------------------------------------------------------ Einstellungen
async function loadSettings() {
  if (S.dirty && S.settings) return renderSettings();
  try {
    S.settings = await api("/api/settings");
    S.draft = structuredClone({ symbols: S.settings.symbols, timeframe: S.settings.timeframe,
      universe: S.settings.universe, ai: { mode: S.settings.ai_mode },
      scanner: Object.fromEntries(S.settings.fields.filter((f) => f.section === "scanner").map((f) => [f.key, f.value])),
      strategy: Object.fromEntries(S.settings.fields.filter((f) => f.section === "strategy").map((f) => [f.key, f.value])),
      risk: Object.fromEntries(S.settings.fields.filter((f) => f.section === "risk").map((f) => [f.key, f.value])) });
    S.dirty = false;
    renderSettings();
  } catch (e) { if (!(e instanceof AuthError)) toast(e.message, "bad"); }
}

function renderSettings() {
  const cfg = S.settings, d = S.draft;
  if (!cfg) return;
  const field = (f) => {
    const v = d[f.section][f.key];
    const shown = f.pct ? +(v * 100).toFixed(3) : v;
    const lo = f.pct ? f.min * 100 : f.min, hi = f.pct ? f.max * 100 : f.max;
    return `<label class="field"><span class="field-label">${esc(f.label)}</span>
      <span class="input-unit"><input type="number" inputmode="decimal" data-sec="${f.section}" data-key="${f.key}" data-pct="${f.pct ? 1 : 0}"
        value="${shown}" min="${+lo.toFixed(3)}" max="${+hi.toFixed(3)}" step="${f.int ? 1 : f.pct ? 0.1 : 0.1}" required>${f.pct ? "<span>%</span>" : ""}</span>
      <span class="field-help">${esc(f.help)} <span class="muted">(${+lo.toFixed(3)}–${+hi.toFixed(3)}${f.pct ? " %" : ""})</span></span></label>`;
  };
  const sec = (name) => cfg.fields.filter((f) => f.section === name).map(field).join("");
  const pairs = [...new Set([...cfg.pairs, ...d.symbols])];
  $("#view-settings").innerHTML = `
    <div class="card"><div class="card-head"><h2>Vorlagen</h2><span class="muted small">Füllt das Formular – gespeichert wird erst mit „Speichern“.</span></div>
      <div class="presets">${PRESETS.map((p, i) => `<button class="preset" type="button" data-preset="${i}"><b>${p.name}</b><span class="muted small">${p.desc}</span></button>`).join("")}</div></div>
    <form id="settings-form" novalidate>
    <div class="card"><div class="card-head"><h2>Handelspaare &amp; Intervall</h2></div>
      <div class="pair-chips" id="pairs">${pairs.map((p) => `<label class="pair-chip ${d.symbols.includes(p) ? "on" : ""}"><input type="checkbox" value="${esc(p)}" ${d.symbols.includes(p) ? "checked" : ""}>${esc(p)}</label>`).join("")}</div>
      <div class="fields spaced">
        <label class="field"><span class="field-label">Weiteres Paar hinzufügen</span>
          <span class="input-unit"><input type="text" id="extra-pair" placeholder="z. B. ATOM/${esc(cfg.quote)}" autocomplete="off"></span>
          <span class="field-help">Enter drücken. Das Paar muss es auf ${esc(cfg.exchange)} in ${esc(cfg.quote)} geben.</span></label>
        <div class="field"><span class="field-label">Kerzen-Intervall</span>
          <div class="seg" id="tf-seg">${cfg.timeframes.map((tf) => `<button type="button" data-tf="${tf}" class="${tf === d.timeframe ? "active" : ""}">${TF_LABEL[tf] || tf}</button>`).join("")}</div>
          <span class="field-help">Längere Kerzen = weniger, ruhigere Trades und weniger Gebühren.</span></div>
      </div></div>
    <div class="card"><div class="card-head"><h2>Weitere Märkte</h2><span class="muted small">Kursdaten von Yahoo Finance · nur Paper-Modus</span></div>
      ${Object.entries(cfg.asset_classes).map(([k, label]) => `<h3 class="spaced">${esc(label)}</h3>
        <div class="pair-chips" data-class="${k}">${(d.universe[k] || []).map((t) => `<label class="pair-chip on"><input type="checkbox" value="${esc(t)}" checked>${esc(t)}</label>`).join("") || '<span class="muted small">keine</span>'}</div>
        <span class="input-unit add-ticker"><input type="text" data-add="${k}" placeholder="Ticker hinzufügen (wie auf finance.yahoo.com) + Enter" autocomplete="off"></span>`).join("")}
      <p class="field-help spaced">Endungen: Xetra <code>.DE</code>, Wien <code>.VI</code>, Paris <code>.PA</code>, Amsterdam <code>.AS</code>, Schweiz <code>.SW</code> · Rohstoffe <code>=F</code> · Devisen z. B. <code>EURUSD=X</code>. Häkchen entfernen = Markt nicht mehr beobachten.</p></div>
    <div class="card"><div class="card-head"><h2>Scanner &amp; KI</h2></div>
      <div class="fields">${sec("scanner")}
        <div class="field"><span class="field-label">KI-Risikomanager</span>
          <div class="seg" id="ai-seg">${Object.entries(cfg.ai_modes).map(([k, l]) => `<button type="button" data-ai="${k}" class="${k === d.ai.mode ? "active" : ""}">${esc(l)}</button>`).join("")}</div>
          <span class="field-help">„Darf Käufe verhindern“: Die KI prüft jedes Kaufsignal und kann es blockieren. „Nur Hinweise“: Sie kommentiert, entscheidet aber nicht. Braucht einen API-Schlüssel – kostenlos mit Google Gemini (Seite „KI“).</span></div>
      </div></div>
    <div class="card"><div class="card-head"><h2>Risiko</h2></div><div class="fields">${sec("risk")}</div>
      <h3 class="spaced">Was das bei deinem Kapital bedeutet</h3><div class="impact" id="impact"></div></div>
    <div class="card"><div class="card-head"><h2>Strategie</h2><span class="muted small">Nach Änderungen am besten neu backtesten (tradingbot backtest).</span></div>
      <div class="fields">${sec("strategy")}</div></div>
    <div class="card lock-note"><span>🔒</span><div><b>Modus: ${cfg.mode === "live" ? "LIVE (echtes Geld)" : "PAPER (virtuelles Geld)"} · Börse: ${esc(cfg.exchange)}</b>
      <div class="muted small">Aus Sicherheitsgründen nur direkt am Server änderbar (<code>tradingbot config</code>). So kann niemand mit deinem Dashboard-Passwort auf echtes Geld umschalten.
      ${cfg.override_active ? "Aktuell gelten im Dashboard gespeicherte Werte." : "Aktuell gelten die Werte aus config.yaml."}</div></div></div>
    <div class="form-actions"><button class="btn btn-primary" type="submit" id="save-btn">Speichern</button>
      <button class="btn btn-ghost" type="button" id="discard-btn">Änderungen verwerfen</button>
      <button class="btn btn-ghost" type="button" id="reset-btn">Auf config.yaml zurücksetzen</button>
      <span class="muted small" id="dirty-note"></span></div>
    </form>`;
  const form = $("#settings-form");
  const markDirty = () => { S.dirty = true; $("#dirty-note").textContent = "● Ungespeicherte Änderungen"; renderImpact(); };
  $$("#pairs input").forEach((cb) => cb.onchange = () => {
    d.symbols = $$("#pairs input").filter((x) => x.checked).map((x) => x.value);
    cb.parentElement.classList.toggle("on", cb.checked);
    markDirty();
  });
  $("#extra-pair").onkeydown = (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const v = e.target.value.trim().toUpperCase();
    if (!/^[A-Z0-9]{2,10}\/[A-Z]{3,4}$/.test(v)) { toast("Format z. B. ATOM/EUR", "bad"); return; }
    if (!d.symbols.includes(v)) d.symbols.push(v);
    S.dirty = true;
    renderSettings();
    $("#dirty-note").textContent = "● Ungespeicherte Änderungen";
  };
  $$(".pair-chips[data-class] input").forEach((cb) => cb.onchange = () => {
    const k = cb.closest("[data-class]").dataset.class;
    d.universe[k] = $$(`.pair-chips[data-class="${k}"] input`).filter((x) => x.checked).map((x) => x.value);
    cb.parentElement.classList.toggle("on", cb.checked);
    markDirty();
  });
  $$("input[data-add]").forEach((inp) => inp.onkeydown = (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const v = inp.value.trim().toUpperCase();
    if (!/^\^?[A-Z0-9][A-Z0-9.\-=]{0,14}$/.test(v) || v.endsWith(".L")) { toast("Ungültiger Ticker (Beispiele: AAPL, SAP.DE, GC=F, EURUSD=X)", "bad"); return; }
    const k = inp.dataset.add;
    d.universe[k] = [...new Set([...(d.universe[k] || []), v])];
    S.dirty = true;
    renderSettings();
    $("#dirty-note").textContent = "● Ungespeicherte Änderungen";
  });
  $$("#ai-seg button").forEach((b) => b.onclick = () => { d.ai.mode = b.dataset.ai; $$("#ai-seg button").forEach((x) => x.classList.toggle("active", x === b)); markDirty(); });
  $$("#tf-seg button").forEach((b) => b.onclick = () => { d.timeframe = b.dataset.tf; $$("#tf-seg button").forEach((x) => x.classList.toggle("active", x === b)); markDirty(); });
  $$("input[data-sec]", form).forEach((inp) => inp.oninput = () => {
    const raw = parseFloat(inp.value.replace(",", "."));
    if (!Number.isNaN(raw)) d[inp.dataset.sec][inp.dataset.key] = inp.dataset.pct === "1" ? raw / 100 : raw;
    markDirty();
  });
  $$("[data-preset]").forEach((b) => b.onclick = () => {
    const p = PRESETS[+b.dataset.preset];
    d.timeframe = p.timeframe;
    Object.assign(d.strategy, p.strategy);
    Object.assign(d.risk, p.risk);
    S.dirty = true;
    renderSettings();
    $("#dirty-note").textContent = `● Vorlage „${p.name}“ eingesetzt – noch nicht gespeichert`;
  });
  form.onsubmit = saveSettings;
  $("#discard-btn").onclick = () => { S.dirty = false; loadSettings(); };
  $("#reset-btn").onclick = async () => {
    if (!await confirmDialog("Auf config.yaml zurücksetzen?", "Alle im Dashboard gespeicherten Einstellungen werden verworfen.", "Zurücksetzen")) return;
    try { toast((await api("/api/settings/reset", { method: "POST", body: {} })).message); S.dirty = false; loadSettings(); }
    catch (e) { if (!(e instanceof AuthError)) toast(e.message, "bad"); }
  };
  if (S.dirty) $("#dirty-note").textContent = "● Ungespeicherte Änderungen";
  renderImpact();
}

function renderImpact() {
  const eq = S.settings?.equity, r = S.draft?.risk;
  if (!r) return;
  const box = $("#impact");
  if (!box) return;
  if (!eq) { box.innerHTML = '<div class="muted">Sobald der Bot sein Kapital kennt, siehst du hier die Beträge.</div>'; return; }
  box.innerHTML = `
    <div><span class="muted small">Max. Verlust pro Trade</span><b class="neg">${money(eq * r.risk_per_trade)}</b></div>
    <div><span class="muted small">Max. Wert einer Position</span><b>${money(eq * r.max_position_pct)}</b></div>
    <div><span class="muted small">Kaufstopp für heute bei</span><b>${money(eq * (1 - r.max_daily_loss))}</b></div>
    <div><span class="muted small">Notbremse bei ca.</span><b class="neg">${money(eq * (1 - r.max_drawdown))}</b></div>`;
}

async function saveSettings(ev) {
  ev.preventDefault();
  const btn = $("#save-btn");
  btn.disabled = true;
  try {
    const r = await api("/api/settings", { method: "POST", body: S.draft });
    S.dirty = false;
    toast(r.message);
    await loadSettings();
  } catch (e) {
    if (!(e instanceof AuthError)) toast("Nicht gespeichert: " + e.message, "bad");
  } finally { btn.disabled = false; }
}

// ------------------------------------------------------------ Protokoll
function renderLog() {
  const st = S.status;
  if (!st) return;
  const kinds = [["all", "Alle"], ["trade", "Trades"], ["warn", "Warnungen"], ["settings", "Einstellungen"]];
  const list = st.events.filter((e) => S.eventFilter === "all" || e.kind === S.eventFilter || (S.eventFilter === "warn" && e.kind === "error"));
  let el = $("#log-events");
  if (!el) {
    $("#view-log").innerHTML = `<div class="card"><div class="card-head"><h2>Ereignisse</h2><div class="seg" id="event-filter"></div></div><div class="events" id="log-events"></div></div>
      <div class="card"><div class="card-head"><h2>Technisches Log</h2><button class="btn btn-secondary btn-sm" id="log-refresh" type="button">Aktualisieren</button></div>
        <pre class="logbox" id="logbox">Lädt…</pre></div>`;
    $("#log-refresh").onclick = loadLogs;
    el = $("#log-events");
  }
  $("#event-filter").innerHTML = kinds.map(([k, l]) => `<button type="button" data-k="${k}" class="${k === S.eventFilter ? "active" : ""}">${l}</button>`).join("");
  $$("#event-filter button").forEach((b) => b.onclick = () => { S.eventFilter = b.dataset.k; renderLog(); });
  el.innerHTML = list.map(eventRow).join("") || '<div class="empty">Keine Ereignisse in dieser Auswahl.</div>';
}
async function loadLogs() {
  try {
    const r = await api("/api/logs?n=300");
    const box = $("#logbox");
    if (!box) return;
    box.textContent = r.lines.join("\n") || "Noch keine Einträge.";
    box.scrollTop = box.scrollHeight;
  } catch (e) { if (!(e instanceof AuthError)) toast(e.message, "bad"); }
}

// ------------------------------------------------------------ Start
function startApp() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  buildNav();
  refreshStatus().then(() => go((location.hash.match(/^#\/(\w+)/) || [])[1] || "overview"));
  S.timers.status = setInterval(() => { if (document.visibilityState === "visible") refreshStatus(); }, 10000);
  S.timers.chart = setInterval(() => { if (document.visibilityState === "visible" && S.view === "chart") loadChart(); }, 30000);
  S.timers.clock = setInterval(updateCountdown, 1000);
}

document.addEventListener("DOMContentLoaded", async () => {
  applyTheme(store.get("theme", "auto"));
  $("#login-form").addEventListener("submit", login);
  $("#logout-btn").addEventListener("click", logout);
  $("#theme-btn").addEventListener("click", cycleTheme);
  $("#menu-theme-btn").addEventListener("click", cycleTheme);
  $("#stop-btn").addEventListener("click", onStopButton);
  $("#pause-btn").addEventListener("click", onPauseButton);
  window.addEventListener("hashchange", () => go((location.hash.match(/^#\/(\w+)/) || [])[1]));
  window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && !$("#app").hidden) refreshStatus(); });
  try {
    const s = await fetch("/api/session", { credentials: "same-origin" }).then((r) => r.json());
    if (s.authenticated) startApp(); else showLogin();
  } catch { showLogin(); }
});
