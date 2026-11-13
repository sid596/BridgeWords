// BRIDGEWORDS frontend.
// Step 1: board, tabs, chips, language picker.
// Step 2: speech via /api/speak, Speak buttons, urgent tiles with pre-cached audio.
"use strict";

const state = {
  lang: null,
  languages: [],
  categories: [],
  tiles: [],
  byId: {},
  activeCat: null,
  selected: [],          // tile ids, in tap order
  mode: "listen",
  quickReplies: ["yes", "no", "later"], // replaced by /api/suggest in step 4
  speaking: false,       // true while our own voice plays (+300 ms): the mic must ignore this
};

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function el(tag, className, children = []) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  for (const child of [].concat(children)) {
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
  return node;
}

function tileFace(tile, wrapperClass) {
  return [el("span", "emoji", tile.emoji), el("span", wrapperClass || "label", tile.label)];
}

let toastTimer;
function toast(msg, ms = 2600) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

function setStatus(text) {
  $("status").textContent = text;
}

function langCode(lang) {
  return state.languages.find((l) => l.id === lang)?.code || "en-IN";
}

// ---------- Audio ----------
// One shared <audio> element. iOS only lets an element play after a user gesture,
// so we "unlock" it on the first touch and reuse it for every later playback.

const player = new Audio();
player.preload = "auto";
const SILENT_WAV =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=";

document.addEventListener(
  "pointerdown",
  () => {
    player.src = SILENT_WAV;
    player.play().catch(() => {});
  },
  { once: true, capture: true }
);

const audioCache = new Map(); // "lang|text" -> Promise<object URL>

function fetchSpeech(text, lang) {
  const key = `${lang}|${text}`;
  if (!audioCache.has(key)) {
    const p = (async () => {
      const t0 = performance.now();
      const res = await fetch("/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, lang }),
      });
      if (!res.ok) {
        let msg = `Speech failed (${res.status})`;
        try {
          msg = (await res.json()).detail || msg;
        } catch {}
        throw new Error(msg);
      }
      const blob = await res.blob();
      console.log(
        `[speak] ${res.headers.get("X-Cache") || "?"} ${Math.round(performance.now() - t0)} ms`,
        lang,
        JSON.stringify(text)
      );
      return URL.createObjectURL(blob);
    })();
    audioCache.set(key, p);
    p.catch(() => audioCache.delete(key)); // let a failed phrase be retried
  }
  return audioCache.get(key);
}

let finishPlayback = null;
let speakToken = 0;

function playUrl(url) {
  return new Promise((resolve, reject) => {
    finishPlayback = resolve;
    player.onended = () => resolve();
    player.onerror = () => reject(new Error("Audio playback failed"));
    player.src = url;
    player.play().catch(reject);
  });
}

function stopSpeaking() {
  const token = ++speakToken;
  player.pause();
  if (window.speechSynthesis) speechSynthesis.cancel();
  if (finishPlayback) finishPlayback();
  finishPlayback = null;
  // Keep the 300 ms half-duplex margin, unless a new phrase has started meanwhile.
  if (state.speaking) sleep(300).then(() => token === speakToken && setSpeaking(false));
}

function setSpeaking(on) {
  state.speaking = on;
  document.body.classList.toggle("is-speaking", on);
  setStatus(on ? "speaking" : "ready");
  // Step 4 hooks the mic here (half-duplex): pause while speaking, resume after.
  document.dispatchEvent(new CustomEvent("speaking", { detail: on }));
}

// Plays text through Timbre. Resolves after playback ends plus 300 ms (half-duplex margin).
async function speak(text, lang = state.lang) {
  stopSpeaking();
  const token = ++speakToken;
  setSpeaking(true);
  try {
    const url = await fetchSpeech(text, lang);
    if (token !== speakToken) return;
    await playUrl(url);
    await sleep(300);
  } finally {
    if (token === speakToken) setSpeaking(false);
  }
}

// Device voice, used only when Timbre is unavailable for an urgent tile.
function speakWithDevice(text, lang) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) return resolve(false);
    const u = new SpeechSynthesisUtterance(text);
    u.lang = langCode(lang);
    u.onend = () => resolve(true);
    u.onerror = () => resolve(false);
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  });
}

// ---------- Urgent tiles ----------

async function prefetchUrgent() {
  const lang = state.lang;
  const urgent = state.tiles.filter((t) => t.urgent);
  const box = $("urgent-state");
  box.textContent = "loading voice…";
  const results = await Promise.allSettled(urgent.map((t) => fetchSpeech(t.say || t.label, lang)));
  if (lang !== state.lang) return; // language changed meanwhile
  const failed = results.find((r) => r.status === "rejected");
  if (failed) {
    console.warn("[urgent] voice not cached:", failed.reason.message);
    box.textContent = "⚠ device voice";
    box.title = failed.reason.message;
  } else {
    box.textContent = "🔊 voice ready";
    box.title = "";
  }
}

async function onUrgent(tile) {
  const text = tile.say || tile.label;
  const lang = state.lang;
  console.log("[urgent]", tile.id, JSON.stringify(text));
  showUrgentOverlay(tile, text);
  try {
    await speak(text, lang);
  } catch (err) {
    console.warn("[urgent] Timbre failed, using device voice:", err.message);
    setSpeaking(true);
    await speakWithDevice(text, lang);
    await sleep(300);
    setSpeaking(false);
  }
}

function showUrgentOverlay(tile, text) {
  $("urgent-overlay-emoji").textContent = tile.emoji;
  $("urgent-overlay-text").textContent = text;
  $("urgent-overlay").hidden = false;
}

// ---------- Data ----------

async function loadSymbols(lang) {
  const url = lang ? `/api/symbols?lang=${encodeURIComponent(lang)}` : "/api/symbols";
  const res = await fetch(url);
  if (!res.ok) throw new Error(`/api/symbols failed: ${res.status}`);
  const data = await res.json();

  state.lang = data.lang;
  state.languages = data.languages;
  state.categories = data.categories;
  state.tiles = data.tiles;
  state.byId = Object.fromEntries(data.tiles.map((t) => [t.id, t]));
  if (!state.categories.includes(state.activeCat)) state.activeCat = state.categories[0];
  document.documentElement.lang = data.lang === "hinglish" ? "hi-Latn" : data.lang;

  renderAll();
  prefetchUrgent();
}

// ---------- Selection ----------

function toggleTile(id) {
  const i = state.selected.indexOf(id);
  if (i === -1) state.selected.push(id);
  else state.selected.splice(i, 1);
  renderGrid();
  renderChips();
}

function removeChip(id) {
  state.selected = state.selected.filter((x) => x !== id);
  renderGrid();
  renderChips();
}

function clearMessage() {
  state.selected = [];
  $("typed").value = "";
  $("results").replaceChildren();
  renderGrid();
  renderChips();
}

function typedText() {
  return $("typed").value.trim();
}

// Plain sentence from the chosen tiles + typed text. This is also the fallback
// when the LLM fails (step 3), so the user can always speak.
function plainSentence() {
  const words = state.selected.map((id) => state.byId[id]?.label).filter(Boolean);
  const typed = typedText();
  if (typed) words.push(typed);
  return words.join(", ");
}

function onSuggest() {
  const text = plainSentence();
  if (!text) return;
  // Step 3 replaces this with /api/suggest (up to 3 sentences or a clarifying question).
  renderSentences([text]);
}

// ---------- Rendering ----------

function renderAll() {
  renderLangPicker();
  renderReplies();
  renderTabs();
  renderGrid();
  renderUrgent();
  renderChips();
}

function renderLangPicker() {
  const sel = $("lang-picker");
  sel.replaceChildren(
    ...state.languages.map((l) => {
      const o = el("option", null, l.name);
      o.value = l.id;
      o.selected = l.id === state.lang;
      return o;
    })
  );
}

function renderReplies() {
  const row = $("reply-row");
  const buttons = state.quickReplies
    .map((id) => state.byId[id])
    .filter(Boolean)
    .map((tile) => {
      const b = el("button", "reply", tileFace(tile));
      b.type = "button";
      b.addEventListener("click", () => toggleTile(tile.id));
      return b;
    });

  // Hard rule 6: every row also offers "Something else" and "Wait".
  for (const id of ["something_else", "wait"]) {
    const tile = state.byId[id];
    if (!tile) continue;
    const b = el("button", "reply escape", tileFace(tile));
    b.type = "button";
    b.addEventListener("click", () =>
      id === "something_else" ? $("tabs").scrollIntoView({ behavior: "smooth" }) : toggleTile(id)
    );
    buttons.push(b);
  }
  row.replaceChildren(...buttons);
}

function renderTabs() {
  $("tabs").replaceChildren(
    ...state.categories.map((cat) => {
      const b = el("button", "tab", cat);
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(cat === state.activeCat));
      b.addEventListener("click", () => {
        state.activeCat = cat;
        renderTabs();
        renderGrid();
      });
      return b;
    })
  );
}

function renderGrid() {
  const tiles = state.tiles.filter((t) => t.category === state.activeCat && !t.urgent);
  $("grid").replaceChildren(
    ...tiles.map((tile) => {
      const selected = state.selected.includes(tile.id);
      const b = el("button", "tile" + (selected ? " selected" : ""), tileFace(tile));
      b.type = "button";
      b.setAttribute("aria-pressed", String(selected));
      b.title = tile.label_en;
      b.addEventListener("click", () => toggleTile(tile.id));
      return b;
    })
  );
}

function renderUrgent() {
  $("urgent-tiles").replaceChildren(
    ...state.tiles
      .filter((t) => t.urgent)
      .map((tile) => {
        const b = el("button", "urgent-tile", tileFace(tile));
        b.type = "button";
        b.addEventListener("click", () => onUrgent(tile));
        return b;
      })
  );
}

function renderChips() {
  const box = $("chips");
  updateSuggestButton();
  if (state.selected.length === 0) {
    box.replaceChildren(el("span", "chips-empty", "Tap tiles to build a message"));
    return;
  }
  box.replaceChildren(
    ...state.selected
      .map((id) => state.byId[id])
      .filter(Boolean)
      .map((tile) => {
        const b = el("button", "chip", [
          el("span", "emoji", tile.emoji),
          el("span", null, tile.label),
          el("span", "x", "✕"),
        ]);
        b.type = "button";
        b.setAttribute("aria-label", `Remove ${tile.label}`);
        b.addEventListener("click", () => removeChip(tile.id));
        return b;
      })
  );
}

function updateSuggestButton() {
  $("suggest-btn").disabled = state.selected.length === 0 && !typedText();
}

// Sentence cards: text + a Speak button. Nothing is spoken until Speak is tapped (hard rule 1).
function renderSentences(sentences) {
  const lang = state.lang;
  $("results").replaceChildren(
    el(
      "div",
      "cards",
      sentences.map((text) => {
        const speakBtn = el("button", "btn btn-speak", "🔊 Speak");
        speakBtn.type = "button";
        speakBtn.addEventListener("click", () => onSpeakCard(speakBtn, text, lang));
        return el("div", "card", [el("p", "card-text", text), speakBtn]);
      })
    )
  );
}

async function onSpeakCard(btn, text, lang) {
  if (btn.classList.contains("playing")) {
    stopSpeaking();
    return;
  }
  document.querySelectorAll(".btn-speak.playing").forEach((b) => {
    b.classList.remove("playing");
    b.textContent = "🔊 Speak";
  });
  btn.classList.add("playing");
  btn.textContent = "⏹ Stop";
  try {
    await speak(text, lang);
  } catch (err) {
    console.warn("[speak] failed:", err.message);
    toast(err.message, 4000);
  } finally {
    btn.classList.remove("playing");
    btn.textContent = "🔊 Speak";
  }
}

// ---------- Wiring ----------

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
  });
}

function init() {
  $("lang-picker").addEventListener("change", (e) => {
    loadSymbols(e.target.value).catch((err) => toast(err.message));
  });
  document.querySelectorAll(".mode").forEach((b) => {
    b.addEventListener("click", () => setMode(b.dataset.mode));
  });
  $("clear-btn").addEventListener("click", clearMessage);
  $("suggest-btn").addEventListener("click", onSuggest);
  $("typed").addEventListener("input", updateSuggestButton);
  $("typed").addEventListener("keydown", (e) => {
    if (e.key === "Enter") onSuggest();
  });
  $("urgent-overlay").addEventListener("click", () => ($("urgent-overlay").hidden = true));

  loadSymbols().catch((err) => {
    console.error(err);
    toast("Could not load the board. Is the server running?");
  });
}

init();
