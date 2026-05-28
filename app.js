"use strict";

/* ──────────────────────────────────────────────────────────
 *  Intermittent Fasting Mini App
 *
 *  Pure vanilla JS. Storage abstraction: Telegram CloudStorage when
 *  available (syncs across devices), DeviceStorage on supported clients,
 *  localStorage otherwise (browser dev / unsupported clients).
 *
 *  State model:
 *    activeFast: { startTs: number, targetHours: number, eatingHours: number } | null
 *    history:    Array<{ startTs: number, endTs: number, targetHours: number }>
 *
 *  Bot sync (optional, soft fail):
 *    When BOT_SYNC_URL is set and we're inside Telegram (initData present),
 *    every start/stop is mirrored to the bot via POST /api/sync so that
 *    push notifications fire automatically.
 * ────────────────────────────────────────────────────────── */

const CONFIG = {
  // Cloudflare Tunnel URL pointing to bot's /api/sync endpoint.
  // Leave empty to disable sync — Mini App works standalone with CloudStorage.
  // To enable: deploy bot/ + cloudflared, paste the public hostname here, redeploy.
  botSyncUrl: "",  // e.g. "https://fasting-bot.example.com" or "https://abc-xyz.trycloudflare.com"
  botDeepLink: "https://t.me/fasting_tracker_ua_bot",
};

const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

/* ─── Storage abstraction ─── */
const storage = (() => {
  const cs = tg?.CloudStorage;
  if (cs && typeof cs.setItem === "function") {
    return {
      kind: "cloud",
      get: (key) => new Promise((res) =>
        cs.getItem(key, (_err, val) => res(val ?? null))),
      set: (key, val) => new Promise((res) =>
        cs.setItem(key, val, () => res())),
      del: (key) => new Promise((res) =>
        cs.removeItem(key, () => res())),
    };
  }
  // Fallback: localStorage
  return {
    kind: "local",
    get: async (key) => localStorage.getItem(key),
    set: async (key, val) => localStorage.setItem(key, val),
    del: async (key) => localStorage.removeItem(key),
  };
})();

const KEY_ACTIVE = "fasting_active";
const KEY_HISTORY = "fasting_history";

async function loadActive() {
  const raw = await storage.get(KEY_ACTIVE);
  if (!raw) return null;
  try { return JSON.parse(raw); }
  catch { return null; }
}

async function saveActive(active) {
  if (active) await storage.set(KEY_ACTIVE, JSON.stringify(active));
  else await storage.del(KEY_ACTIVE);
}

async function loadHistory() {
  const raw = await storage.get(KEY_HISTORY);
  if (!raw) return [];
  try { return JSON.parse(raw) ?? []; }
  catch { return []; }
}

async function saveHistory(history) {
  // CloudStorage values capped at 4096 bytes per item — keep last 100 entries.
  const trimmed = history.slice(-100);
  await storage.set(KEY_HISTORY, JSON.stringify(trimmed));
}

/* ─── Format helpers ─── */
function fmtDuration(ms) {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function fmtDateUk(ts) {
  const d = new Date(ts);
  return d.toLocaleDateString("uk-UA", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function fmtHoursShort(ms) {
  const h = ms / 3_600_000;
  if (h < 1) return `${Math.round(h * 60)} хв`;
  return `${h.toFixed(1)} год`;
}

/* ─── Tab switching ─── */
const tabs = document.querySelectorAll(".tab");
const screens = document.querySelectorAll(".screen");

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.tab;
    tabs.forEach((t) => t.classList.toggle("active", t === tab));
    screens.forEach((s) => s.classList.toggle("active", s.id === `tab-${target}`));
    if (target === "history") renderHistory();
    if (target === "stats") renderStats();
  });
});

/* ─── Bot sync (soft-fail) ─── */

function protocolKey(targetHours, eatingHours) {
  // Map (target, eating) → short bot protocol key.
  const m = {
    "16_8": "16", "18_6": "18", "20_4": "20", "23_1": "omad",
  };
  return m[`${targetHours}_${eatingHours}`] || null;
}

async function syncToBot(action, body = {}) {
  if (!CONFIG.botSyncUrl || !tg?.initData) {
    // Sync not configured or not inside Telegram — no-op
    return { ok: false, reason: "sync-disabled" };
  }
  try {
    const url = CONFIG.botSyncUrl.replace(/\/+$/, "") + "/api/sync";
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": tg.initData,
      },
      body: JSON.stringify({ action, ...body }),
      signal: AbortSignal.timeout(5000),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.ok) {
      console.log("[sync]", action, "OK");
      return { ok: true, ...data };
    }
    console.warn("[sync] failed:", resp.status, data);
    return { ok: false, status: resp.status, ...data };
  } catch (e) {
    console.warn("[sync] error:", e.message);
    return { ok: false, error: e.message };
  }
}

function updateSyncBadge(state) {
  const el = document.getElementById("sync-badge");
  if (!el) return;
  if (!CONFIG.botSyncUrl) {
    el.textContent = "";
    el.hidden = true;
    return;
  }
  el.hidden = false;
  if (state === "ok") {
    el.textContent = "🔔 Нагадування активні";
    el.className = "sync-badge ok";
  } else if (state === "fail") {
    el.textContent = "⚠️ Бот недоступний";
    el.className = "sync-badge fail";
  } else {
    el.textContent = "";
    el.className = "sync-badge";
  }
}

/* ─── Start a fast ─── */
async function startFast(targetHours, eatingHours) {
  const active = {
    startTs: Date.now(),
    targetHours,
    eatingHours,
  };
  await saveActive(active);
  showActive(active);
  // Fire-and-forget sync to bot for reminder scheduling
  const proto = protocolKey(targetHours, eatingHours);
  if (proto) {
    syncToBot("start", { protocol: proto }).then((r) => {
      updateSyncBadge(r.ok ? "ok" : "fail");
    });
  }
}

document.querySelectorAll(".protocol-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const h = Number(btn.dataset.hours);
    const e = Number(btn.dataset.eating);
    await startFast(h, e);
    tg?.HapticFeedback?.impactOccurred?.("medium");
  });
});

/* ─── Custom interval dialog ─── */
const dialog = document.getElementById("custom-dialog");
const customHoursInput = document.getElementById("custom-hours");

document.getElementById("custom-btn").addEventListener("click", () => {
  if (typeof dialog.showModal === "function") dialog.showModal();
  else {
    // very old browser fallback — prompt()
    const h = parseInt(prompt("Години голодування:", "14"), 10);
    if (h > 0 && h < 73) startFast(h, 24 - h);
  }
});

document.getElementById("custom-cancel").addEventListener("click", (e) => {
  e.preventDefault();
  dialog.close();
});

dialog.addEventListener("close", () => {
  if (dialog.returnValue !== "default") return;
});

document.querySelector("#custom-dialog form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const h = Number(customHoursInput.value);
  if (h > 0 && h < 73) {
    dialog.close();
    await startFast(h, Math.max(1, 24 - h));
  }
});

/* ─── Stop active fast ─── */
document.getElementById("stop-btn").addEventListener("click", async () => {
  const active = await loadActive();
  if (!active) return;
  if (tg?.showConfirm) {
    tg.showConfirm("Завершити голодування?", async (ok) => {
      if (ok) await finishFast(active);
    });
  } else {
    if (confirm("Завершити голодування?")) await finishFast(active);
  }
});

async function finishFast(active) {
  const history = await loadHistory();
  history.push({
    startTs: active.startTs,
    endTs: Date.now(),
    targetHours: active.targetHours,
  });
  await saveHistory(history);
  await saveActive(null);
  showIdle();
  tg?.HapticFeedback?.notificationOccurred?.("success");
  syncToBot("stop").then((r) => {
    updateSyncBadge(r.ok ? null : "fail");
  });
}

/* ─── Timer rendering loop ─── */
let tickHandle = null;

function showIdle() {
  document.getElementById("idle-view").hidden = false;
  document.getElementById("active-view").hidden = true;
  if (tickHandle) { clearInterval(tickHandle); tickHandle = null; }
}

function showActive(active) {
  document.getElementById("idle-view").hidden = true;
  document.getElementById("active-view").hidden = false;
  document.getElementById("protocol-label").textContent =
    `Протокол ${active.targetHours}:${active.eatingHours}`;
  renderTick(active);
  if (tickHandle) clearInterval(tickHandle);
  tickHandle = setInterval(() => renderTick(active), 1000);
}

const ringFg = document.querySelector(".ring-fg");
const RING_CIRCUMFERENCE = 2 * Math.PI * 90;

function renderTick(active) {
  const now = Date.now();
  const elapsedMs = now - active.startTs;
  const targetMs = active.targetHours * 3_600_000;
  const ratio = Math.min(1, elapsedMs / targetMs);

  document.getElementById("timer-elapsed").textContent = fmtDuration(elapsedMs);
  document.getElementById("timer-target").textContent = `з ${active.targetHours}:00:00`;

  // Ring progress
  ringFg.style.strokeDashoffset = String(RING_CIRCUMFERENCE * (1 - ratio));
  ringFg.classList.toggle("complete", ratio >= 1);

  // Linear bar
  const fill = document.getElementById("progress-fill");
  fill.style.width = `${ratio * 100}%`;
  fill.classList.toggle("complete", ratio >= 1);

  // Phase label
  const phase = document.getElementById("phase-label");
  if (ratio >= 1) {
    const overMs = elapsedMs - targetMs;
    phase.textContent = `✓ Ціль досягнута. Понад: ${fmtDuration(overMs)}`;
    phase.classList.add("complete");
  } else {
    const remainMs = targetMs - elapsedMs;
    phase.textContent = `Залишилось ${fmtDuration(remainMs)}`;
    phase.classList.remove("complete");
  }
}

/* ─── History rendering ─── */
async function renderHistory() {
  const history = await loadHistory();
  const list = document.getElementById("history-list");
  const empty = document.getElementById("history-empty");
  list.innerHTML = "";
  if (history.length === 0) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  // Newest first
  for (const entry of [...history].reverse()) {
    const li = document.createElement("li");
    const dur = entry.endTs - entry.startTs;
    const targetMs = entry.targetHours * 3_600_000;
    const completed = dur >= targetMs;
    li.innerHTML = `
      <div>
        <div class="hist-date">${fmtDateUk(entry.startTs)}</div>
        <div class="muted" style="font-size:12px">ціль ${entry.targetHours} год</div>
      </div>
      <div class="hist-dur ${completed ? "completed" : ""}">${fmtHoursShort(dur)}</div>
    `;
    list.appendChild(li);
  }
}

/* ─── Stats rendering ─── */
async function renderStats() {
  const history = await loadHistory();
  const total = history.length;
  document.getElementById("stat-count").textContent = String(total);

  if (total === 0) {
    document.getElementById("stat-longest").textContent = "—";
    document.getElementById("stat-avg").textContent = "—";
    document.getElementById("stat-streak").textContent = "0";
    return;
  }

  let longestMs = 0;
  let totalMs = 0;
  for (const e of history) {
    const d = e.endTs - e.startTs;
    if (d > longestMs) longestMs = d;
    totalMs += d;
  }
  document.getElementById("stat-longest").textContent = fmtHoursShort(longestMs);
  document.getElementById("stat-avg").textContent = fmtHoursShort(totalMs / total);

  // Streak = consecutive days with at least 1 completed fast (ending on that day)
  const completedDays = new Set();
  for (const e of history) {
    const dur = e.endTs - e.startTs;
    if (dur < e.targetHours * 3_600_000) continue; // only completed counts
    const d = new Date(e.endTs);
    const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    completedDays.add(key);
  }
  let streak = 0;
  const cursor = new Date();
  cursor.setHours(0, 0, 0, 0);
  for (;;) {
    const key = `${cursor.getFullYear()}-${cursor.getMonth()}-${cursor.getDate()}`;
    if (completedDays.has(key)) {
      streak++;
      cursor.setDate(cursor.getDate() - 1);
    } else {
      // Allow today to have no entry yet — start counting from yesterday
      if (streak === 0 && cursor.getTime() === new Date().setHours(0, 0, 0, 0)) {
        cursor.setDate(cursor.getDate() - 1);
        continue;
      }
      break;
    }
  }
  document.getElementById("stat-streak").textContent = String(streak);
}

/* ─── Boot ─── */
(async function init() {
  const active = await loadActive();
  if (active) showActive(active);
  else showIdle();

  // Show storage kind in dev (helps when testing in browser)
  if (storage.kind === "local") {
    console.log("[fasting] Using localStorage fallback (not inside Telegram or CloudStorage unavailable)");
  } else {
    console.log("[fasting] Using Telegram CloudStorage");
  }

  // Wire up donation buttons (in FAQ tab) — open bot chat
  document.querySelectorAll("[data-bot-cmd]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const cmd = btn.dataset.botCmd;
      const url = `${CONFIG.botDeepLink}?start=${cmd}`;
      if (tg?.openTelegramLink) tg.openTelegramLink(url);
      else window.open(url, "_blank");
    });
  });
})();
