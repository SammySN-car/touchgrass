"use strict";

const DB_NAME = "grass";
const DB_VERSION = 1;
const STORE_NAME = "pending";
const RANKS = ["F", "E", "D", "C", "B", "A", "S"];
const THRESHOLDS = [0, 100, 250, 500, 850, 1300, 2000];
const STATE_URL = "/api/state";
const EVIDENCE_URL = "/api/evidence";
const LOG_URL = "/api/log";

let currentState = null;
let currentQuestId = null;
let flushInFlight = false;
let thumbUrls = [];

function $(id) {
  return document.getElementById(id);
}

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id", autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function txDone(tx, db) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error);
    };
    tx.onabort = () => {
      db.close();
      reject(tx.error || new Error("transaction aborted"));
    };
  });
}

async function readAllRows() {
  const db = await openDb();
  const rows = [];
  await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).openCursor();
    req.onsuccess = () => {
      const cursor = req.result;
      if (cursor) {
        rows.push(cursor.value);
        cursor.continue();
      }
    };
    req.onerror = () => reject(req.error);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("transaction aborted"));
  }).catch((err) => {
    db.close();
    throw err;
  });
  db.close();
  return rows;
}

async function savePending(files, questId) {
  const list = Array.from(files || []);
  if (list.length === 0) return;
  const db = await openDb();
  const tx = db.transaction(STORE_NAME, "readwrite");
  const store = tx.objectStore(STORE_NAME);
  for (const file of list) {
    store.put({
      quest_id: questId,
      blob: file,
      captured_at: new Date().toISOString(),
    });
  }
  await txDone(tx, db);
  await updatePendingBadge();
  renderBufferList();
}

async function getPending(questId) {
  const rows = await readAllRows();
  return rows.filter((row) => row.quest_id === questId);
}

async function clearPending(ids) {
  const list = Array.isArray(ids) ? ids : [];
  if (list.length > 0) {
    const db = await openDb();
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    for (const id of list) {
      store.delete(id);
    }
    await txDone(tx, db);
  }
  await updatePendingBadge();
  renderBufferList();
}

async function updatePendingBadge() {
  let rows = [];
  try {
    rows = await readAllRows();
  } catch (err) {
    return;
  }
  const badge = $("pending-badge");
  if (badge) {
    if (rows.length > 0) {
      badge.hidden = false;
      badge.textContent = rows.length + " PENDING - submit when back in range";
    } else {
      badge.hidden = true;
      badge.textContent = "";
    }
  }
  const chip = $("quest-pending-chip");
  if (chip) {
    const forQuest =
      currentQuestId === null
        ? 0
        : rows.filter((row) => row.quest_id === currentQuestId).length;
    chip.hidden = forQuest === 0;
    chip.textContent = forQuest > 0 ? forQuest + " PENDING" : "";
  }
}

async function fetchState() {
  const res = await fetch(STATE_URL, { cache: "no-store" });
  if (!res.ok) throw new Error("state " + res.status);
  return res.json();
}

async function flushPending() {
  if (flushInFlight) return;
  flushInFlight = true;
  try {
    const rows = await readAllRows();
    if (rows.length === 0) {
      await updatePendingBadge();
      return;
    }
    setJudging(true);
    const groups = new Map();
    for (const row of rows) {
      const key = row.quest_id;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row);
    }
    for (const [questId, group] of groups) {
      let state;
      try {
        state = await fetchState();
      } catch (err) {
        return;
      }
      const currentId = state.quest ? state.quest.id : null;
      if (currentId === null || questId !== currentId) continue;
      const fd = new FormData();
      fd.append("quest_id", String(questId));
      let newest = group[0].captured_at;
      for (const row of group) {
        if (row.captured_at > newest) newest = row.captured_at;
      }
      fd.append("captured_at", newest);
      for (const row of group) {
        fd.append("files", row.blob, "photo.jpg");
      }
      let res;
      try {
        res = await fetch(EVIDENCE_URL, { method: "POST", body: fd });
      } catch (err) {
        return;
      }
      const ids = group.map((row) => row.id);
      if (res.ok) {
        let verdict = null;
        try {
          verdict = await res.json();
        } catch (err) {
          verdict = null;
        }
        await clearPending(ids);
        if (!verdict) {
          try {
            const fresh = await fetchState();
            verdict = fresh.verdict;
          } catch (err) {
            verdict = null;
          }
        }
        showVerdict(verdict, false);
        loadState();
        return;
      }
      if (res.status === 409) {
        await clearPending(ids);
        let existing = null;
        try {
          const fresh = await fetchState();
          existing = fresh.verdict;
        } catch (err) {
          existing = null;
        }
        showVerdict(existing, true);
        loadState();
        return;
      }
      return;
    }
  } catch (err) {
    return;
  } finally {
    flushInFlight = false;
    setJudging(false);
    updatePendingBadge();
  }
}

function setJudging(on) {
  const el = $("judging");
  if (el) el.hidden = !on;
  const btn = $("submit-pending");
  if (btn) btn.disabled = !!on;
}

function showScreen(name) {
  const screens = document.querySelectorAll(".screen");
  for (const screen of screens) {
    screen.hidden = screen.id !== "screen-" + name;
  }
  const tabs = document.querySelectorAll(".tab");
  for (const tab of tabs) {
    tab.classList.toggle("active", tab.dataset.screen === name);
  }
  if (name === "evidence") renderBufferList();
  if (name === "log") loadLog();
}

function renderQuest() {
  const state = currentState;
  if (!state) {
    const status = $("quest-status");
    if (status) {
      status.hidden = false;
      status.textContent = "NO LINK - STATE UNAVAILABLE";
    }
    return;
  }
  const status = $("quest-status");
  if (status) {
    status.hidden = true;
    status.textContent = "";
  }
  const player = state.player || {};
  $("rank").textContent = player.rank || "F";
  $("streak").textContent = String(player.streak == null ? 0 : player.streak);
  const xp = Number(player.xp || 0);
  let idx = RANKS.indexOf(player.rank);
  if (idx < 0) idx = 0;
  const floor = THRESHOLDS[idx];
  let ceil = null;
  let pct = 100;
  if (idx < THRESHOLDS.length - 1) {
    ceil = THRESHOLDS[idx + 1];
    pct = ceil > floor ? ((xp - floor) / (ceil - floor)) * 100 : 100;
  }
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  $("xp-fill").style.width = pct + "%";
  $("xp-label").textContent = xp + " XP";
  $("xp-next").textContent = ceil === null ? "MAX RANK" : "NEXT " + ceil;
  const quest = state.quest;
  const captureBtn = $("capture-btn");
  if (!quest) {
    $("quest-title").textContent = "NO QUEST ISSUED";
    $("quest-objective").textContent = "";
    $("quest-target").textContent = "";
    $("quest-flavor").textContent = "";
    captureBtn.disabled = true;
    return;
  }
  captureBtn.disabled = false;
  $("quest-title").textContent = quest.title || "";
  $("quest-objective").textContent = quest.objective || "";
  const target = Number(quest.target_count || 1);
  $("quest-target").textContent =
    "TARGET: " + target + (target === 1 ? " PHOTO" : " PHOTOS");
  $("quest-flavor").textContent = quest.flavor || "";
}

function renderBufferList() {
  const listEl = $("buffer-list");
  const countEl = $("buffer-count");
  if (!listEl || !countEl) return;
  readAllRows()
    .then((rows) => {
      for (const url of thumbUrls) URL.revokeObjectURL(url);
      thumbUrls = [];
      listEl.textContent = "";
      countEl.textContent = rows.length + " IN BUFFER";
      if (rows.length === 0) {
        const empty = document.createElement("div");
        empty.className = "buffer-empty";
        empty.textContent = "BUFFER EMPTY";
        listEl.appendChild(empty);
        return;
      }
      for (const row of rows) {
        const item = document.createElement("div");
        item.className = "buffer-item";
        const img = document.createElement("img");
        const url = URL.createObjectURL(row.blob);
        thumbUrls.push(url);
        img.src = url;
        img.alt = "pending evidence";
        const meta = document.createElement("div");
        meta.className = "meta";
        const scope =
          row.quest_id === currentQuestId ? "" : "QUEST " + row.quest_id + " ";
        meta.textContent = scope + String(row.captured_at || "").replace("T", " ");
        item.appendChild(img);
        item.appendChild(meta);
        listEl.appendChild(item);
      }
    })
    .catch(() => {});
}

function normalizeVerdict(payload) {
  if (!payload) return null;
  let events = payload.events;
  if (!Array.isArray(events) && typeof payload.events_json === "string") {
    try {
      events = JSON.parse(payload.events_json);
    } catch (err) {
      events = [];
    }
  }
  if (!Array.isArray(events)) events = [];
  const passed = payload.passed === true || payload.passed === 1;
  return {
    passed: passed,
    line: payload.line || "",
    xp_delta: Number(payload.xp_delta || 0),
    rank_before: payload.rank_before || "",
    rank_after: payload.rank_after || "",
    model_used: payload.model_used || "",
    streak:
      payload.streak != null
        ? Number(payload.streak)
        : Number(payload.streak_after || 0),
    events: events,
  };
}

function showVerdict(payload, alreadyJudged) {
  const verdict = normalizeVerdict(payload);
  const note = $("verdict-note");
  const banner = $("verdict-banner");
  const line = $("verdict-line");
  const xpEl = $("verdict-xp");
  const eventsEl = $("verdict-events");
  note.hidden = !alreadyJudged;
  note.textContent = alreadyJudged ? "the System already judged this" : "";
  eventsEl.textContent = "";
  if (!verdict) {
    banner.className = "banner";
    banner.textContent = "JUDGED";
    line.textContent = alreadyJudged ? "the System already judged this" : "";
    xpEl.textContent = "";
    showScreen("verdict");
    return;
  }
  banner.className = "banner " + (verdict.passed ? "pass" : "penalty");
  banner.textContent = verdict.passed ? "PASS" : "PENALTY";
  line.textContent = verdict.line;
  const delta = verdict.xp_delta;
  xpEl.textContent =
    (delta > 0 ? "+" : "") + delta + " XP   STREAK " + verdict.streak;
  for (const event of verdict.events) {
    const li = document.createElement("li");
    if (event.kind === "RANK_UP") li.className = "rank-up";
    li.textContent = (event.kind || "") + " " + (event.detail || "");
    eventsEl.appendChild(li);
  }
  showScreen("verdict");
}

async function loadState() {
  try {
    const state = await fetchState();
    currentState = state;
    currentQuestId = state.quest ? state.quest.id : null;
    renderQuest();
    await updatePendingBadge();
  } catch (err) {
    currentState = null;
    currentQuestId = null;
    renderQuest();
  }
}

function renderLog(entries) {
  const listEl = $("log-list");
  if (!listEl) return;
  listEl.textContent = "";
  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "log-empty";
    empty.textContent = "NO VERDICTS RECORDED";
    listEl.appendChild(empty);
    return;
  }
  for (const entry of entries) {
    const item = document.createElement("div");
    item.className = "log-entry";
    const head = document.createElement("div");
    head.className = "log-head";
    const title = document.createElement("span");
    title.className = "log-title";
    title.textContent = entry.title || "";
    const state = document.createElement("span");
    const passed = entry.passed === 1 || entry.passed === true;
    state.className = "log-state " + (passed ? "pass" : "penalty");
    state.textContent = passed ? "PASS" : "PENALTY";
    const day = document.createElement("div");
    day.className = "log-day";
    day.textContent = (entry.quest_day || "") + "   " + (entry.created_at || "");
    const line = document.createElement("div");
    line.className = "log-line";
    line.textContent = entry.line || "";
    const model = document.createElement("div");
    model.className = "log-model";
    model.textContent = "model_used: " + (entry.model_used || "unknown");
    head.appendChild(title);
    head.appendChild(state);
    item.appendChild(head);
    item.appendChild(day);
    item.appendChild(line);
    item.appendChild(model);
    listEl.appendChild(item);
  }
}

async function loadLog() {
  const listEl = $("log-list");
  if (!listEl) return;
  try {
    const res = await fetch(LOG_URL, { cache: "no-store" });
    if (!res.ok) throw new Error("log " + res.status);
    const data = await res.json();
    renderLog(Array.isArray(data.entries) ? data.entries : []);
  } catch (err) {
    listEl.textContent = "";
    const empty = document.createElement("div");
    empty.className = "log-empty";
    empty.textContent = "NO LINK - LOG UNAVAILABLE";
    listEl.appendChild(empty);
  }
}

function wire() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => showScreen(tab.dataset.screen));
  });
  $("capture-btn").addEventListener("click", () => {
    showScreen("evidence");
    $("file-input").focus();
  });
  $("file-input").addEventListener("change", (event) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    if (currentQuestId === null) {
      event.target.value = "";
      const status = $("quest-status");
      if (status) {
        status.hidden = false;
        status.textContent = "NO QUEST - PHOTOS NOT BUFFERED";
        showScreen("quest");
      }
      return;
    }
    savePending(files, currentQuestId).catch(() => {});
    event.target.value = "";
  });
  $("submit-pending").addEventListener("click", () => {
    flushPending();
  });
  $("verdict-return").addEventListener("click", () => showScreen("quest"));
  $("verdict-log").addEventListener("click", () => showScreen("log"));
  $("log-return").addEventListener("click", () => showScreen("quest"));
  window.addEventListener("online", flushPending);
}

async function init() {
  wire();
  await loadState();
  await updatePendingBadge();
  renderBufferList();
  await flushPending();
}

init();
