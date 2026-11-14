// BridgeWords frontend (vanilla JS, no build step).
// Board + chips, speech via /api/speak, suggestions via /api/suggest,
// listening via mic + voice-activity detection + /api/listen, phone mode,
// translation, board learning, settings for motor access (tile size, tap guard,
// switch scanning), recently said, show-to-partner, demo mode, offline banner.
"use strict";

const state = {
  lang: null,
  languages: [],
  categories: [],
  tiles: [],
  byId: {},
  activeCat: null,
  selected: [],          // tile ids, or "txt:<label>" for quick replies that aren't tiles
  mode: "listen",
  history: [],           // last partner lines, oldest first
  quickReplies: ["yes", "no", "later"],
  speaking: false,       // true while our own voice plays (+300 ms): the mic ignores this
  editMode: false,
  hiddenCount: 0,
  ideas: [],             // AI tile proposals waiting for the user's decision
  ui: {},                // interface text in the board language (data/ui.json)
  partnerLanguages: [],  // every language the partner may speak (all Gnani languages)
  partnerSetting: "",    // "" = same as mine, "auto" = detect, or a language id
  partnerDetected: null, // last language Prisma heard in auto mode
  recent: [],            // last sentences the user spoke: [{shown, text, lang}]
};

// Per-device settings (localStorage; the app works without it).
const settings = { size: "normal", guard: 400, scan: 0, partner: "", mode: "listen" };

function loadSettings() {
  try {
    Object.assign(settings, JSON.parse(localStorage.getItem("bw-settings") || "{}"));
    state.recent = JSON.parse(localStorage.getItem("bw-recent") || "[]").slice(0, 5);
  } catch {}
}

function saveSettings() {
  try {
    localStorage.setItem("bw-settings", JSON.stringify(settings));
    localStorage.setItem("bw-recent", JSON.stringify(state.recent));
  } catch {}
}

// Fitzgerald Key colour per category (as on AAC devices).
const CATEGORY_KEY = {
  "Quick replies": "social",
  Feelings: "feeling",
  "Food and drink": "thing",
  "Health and body": "thing",
  People: "people",
  "Places and things": "thing",
  "Needs and time": "action",
  "My tiles": "mine",
};
const keyFor = (category) => CATEGORY_KEY[category] || "thing";

// Interface text in the board language, falling back to the key.
function t(key) {
  return state.ui[key] ?? key;
}

function langName(id) {
  return state.partnerLanguages.find((l) => l.id === id)?.name || id;
}

// The partner's language for this conversation (null = same as the user's).
function partnerLang() {
  if (state.partnerSetting === "auto") return state.partnerDetected;
  return state.partnerSetting || null;
}

const MAX_HISTORY = 6;
const TXT = "txt:";

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

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

// Tremor guard: a second tap on the same control within settings.guard ms is ignored.
const lastTap = new Map();
function isRepeatTap(key) {
  if (!settings.guard || !key) return false;
  const now = performance.now();
  const prev = lastTap.get(key) || 0;
  lastTap.set(key, now);
  return now - prev < settings.guard;
}

function button(className, children, onClick, guardKey = null) {
  const b = el("button", className, children);
  b.type = "button";
  b.addEventListener("click", (e) => {
    if (isRepeatTap(guardKey)) return console.log(`[tap] ignored repeat on ${guardKey}`);
    onClick(e);
  });
  return b;
}

function tileFace(tile) {
  return [el("span", "emoji", tile.emoji), el("span", "label", tile.label)];
}

let toastTimer;
function toast(msg, ms = 2600) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

// Status pill: listening / hearing / thinking / speaking / ready / mic off.
let busy = 0;
function refreshStatus() {
  let s = mic.on ? "listening" : "ready";
  if (mic.on && mic.recording) s = "hearing";
  if (busy > 0) s = "thinking";
  if (state.speaking) s = "speaking";
  $("status-text").textContent = (state.ui.status && state.ui.status[s]) || s;
  $("status").dataset.state = s;
}

async function thinking(fn) {
  busy++;
  refreshStatus();
  try {
    return await fn();
  } finally {
    busy--;
    refreshStatus();
  }
}

// ---------- Debug strip (tap the status pill, or open with ?debug=1) ----------

const debug = {
  on: false,
  lines: {},
  level: 0,
  threshold: 0,
  frame: 0,

  init() {
    let saved = null;
    try {
      saved = localStorage.getItem("bw-debug");
    } catch {}
    this.set(new URLSearchParams(location.search).has("debug") || saved === "1");
    const toggle = () => this.set(!this.on);
    $("status").addEventListener("click", toggle);
    $("debug-btn").addEventListener("click", toggle);
    $("status").addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && toggle());
  },

  set(on) {
    this.on = on;
    $("debug").hidden = !on;
    try {
      localStorage.setItem("bw-debug", on ? "1" : "0");
    } catch {}
    this.render();
  },

  note(key, text) {
    this.lines[key] = text;
    this.render();
  },

  meter(rms, threshold) {
    this.level = rms;
    this.threshold = threshold;
    if (!this.on || this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      const pct = (v) => `${Math.min(100, Math.sqrt(v / 0.2) * 100).toFixed(1)}%`; // sqrt: quiet sounds visible
      $("debug-bar").style.width = pct(this.level);
      $("debug-thr").style.left = pct(this.threshold);
    });
  },

  render() {
    if (!this.on) return;
    const order = ["heard", "timing", "suggest", "speak", "learn"];
    const text = order.filter((k) => this.lines[k]).map((k) => this.lines[k]).join("  ·  ");
    $("debug-text").textContent = text || (mic.on ? "listening…" : "mic off");
  },
};

function langCode(lang) {
  const all = [...state.languages, ...state.partnerLanguages];
  return all.find((l) => l.id === lang)?.code || "en-IN";
}

async function postJSON(url, body) {
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    setOffline(true); // the request never reached our server
    throw err;
  }
  if (!res.ok) throw new Error(await errorText(res));
  const data = await res.json();
  if (url === "/api/suggest") setOffline(!!data.fallback && !navigator.onLine);
  return data;
}

function setOffline(on) {
  $("offline-banner").hidden = !on;
}

async function errorText(res) {
  try {
    const d = (await res.json()).detail;
    return typeof d === "string" ? d : `Request failed (${res.status})`;
  } catch {
    return `Request failed (${res.status})`;
  }
}

// ======================================================================
// Speech out
// One shared <audio> element. iOS only lets an element play after a user
// gesture, so it is "unlocked" on the first touch and reused afterwards.
// ======================================================================

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
      if (!res.ok) throw new Error(await errorText(res));
      const blob = await res.blob();
      debug.note("speak", `speak ${res.headers.get("X-Cache") || "?"} ${Math.round(performance.now() - t0)} ms`);
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

function setSpeaking(on) {
  state.speaking = on;
  document.body.classList.toggle("is-speaking", on);
  if (on) mic.abortSegment(); // half-duplex: never record our own voice
  refreshStatus();
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

// Device voice: used only when Timbre is unavailable for a fixed phrase.
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

// Fixed phrases (urgent tiles, Wait): one tap speaks a known phrase, no AI step.
// If the partner speaks another language and the phrase exists in it, say it in theirs.
function fixedPhrase(tile) {
  const pl = partnerLang();
  if (pl && tile.say_by_lang && tile.say_by_lang[pl]) return { text: tile.say_by_lang[pl], lang: pl };
  return { text: tile.say || tile.label, lang: state.lang };
}

async function speakFixed(tile) {
  const { text, lang } = fixedPhrase(tile);
  try {
    await speak(text, lang);
  } catch (err) {
    console.warn(`[speak] Timbre failed for ${tile.id}, using device voice:`, err.message);
    setSpeaking(true);
    await speakWithDevice(text, lang);
    await sleep(300);
    setSpeaking(false);
  }
}

// Fetch fixed phrases one at a time: firing them all at once trips Timbre's rate limit (HTTP 429),
// which would leave an urgent tile without its audio.
let prefetchChain = Promise.resolve();

function prefetchFixed() {
  prefetchChain = prefetchChain.then(prefetchFixedNow, prefetchFixedNow);
  return prefetchChain;
}

async function prefetchFixedNow() {
  const lang = state.lang;
  const box = $("urgent-state");
  box.textContent = t("voice_loading");
  let failed = null;
  for (const tile of state.tiles.filter((x) => x.say)) {
    const p = fixedPhrase(tile);
    try {
      await fetchSpeech(p.text, p.lang);
    } catch (err) {
      failed = err;
    }
  }
  if (lang !== state.lang) return; // language changed meanwhile
  box.textContent = failed ? t("device_voice") : t("voice_ready");
  box.title = failed ? failed.message : "";
  if (failed) console.warn("[urgent] voice not cached:", failed.message);
}

function onUrgent(tile) {
  console.log("[urgent]", tile.id);
  $("urgent-overlay-emoji").textContent = tile.emoji;
  $("urgent-overlay-text").textContent = fixedPhrase(tile).text;
  $("urgent-overlay").hidden = false;
  speakFixed(tile);
}

function onWait() {
  const tile = state.byId.wait;
  if (tile) speakFixed(tile);
}

// ======================================================================
// Board data + selection
// ======================================================================

async function loadSymbols(lang) {
  const url = lang ? `/api/symbols?lang=${encodeURIComponent(lang)}` : "/api/symbols";
  const res = await fetch(url);
  if (!res.ok) throw new Error(await errorText(res));
  const data = await res.json();

  state.lang = data.lang;
  state.languages = data.languages;
  state.partnerLanguages = data.partner_languages || [];
  state.ui = data.ui || {};
  state.categories = data.categories;
  state.tiles = data.tiles;
  state.byId = Object.fromEntries(data.tiles.map((t) => [t.id, t]));
  state.hiddenCount = data.hidden_count || 0;
  state.selected = state.selected.filter((k) => k.startsWith(TXT) || state.byId[k]); // drop hidden tiles
  if (!state.categories.includes(state.activeCat)) state.activeCat = state.categories[0];
  document.documentElement.lang = data.lang === "hinglish" ? "hi-Latn" : data.lang;

  renderAll();
  prefetchFixed();
  loadIdeas();
}

function chipInfo(key) {
  if (key.startsWith(TXT)) return { emoji: "", label: key.slice(TXT.length) };
  return state.byId[key];
}

function toggleSelected(key) {
  const i = state.selected.indexOf(key);
  if (i === -1) state.selected.push(key);
  else state.selected.splice(i, 1);
  selectionChanged();
}

function selectionChanged() {
  $("results").replaceChildren(); // suggestions no longer match the chips
  renderGrid();
  renderChips();
}

function clearMessage() {
  state.selected = [];
  $("typed").value = "";
  selectionChanged();
}

function typedText() {
  return $("typed").value.trim();
}

function selectionPayload() {
  const tiles = state.selected.filter((k) => !k.startsWith(TXT));
  const extra = state.selected.filter((k) => k.startsWith(TXT)).map((k) => k.slice(TXT.length));
  const typed = [...extra, typedText()].filter(Boolean).join(", ");
  return { tiles, typed };
}

// ======================================================================
// Suggestions
// ======================================================================

let suggestToken = 0;

async function onSuggest(clarification = null) {
  const { tiles, typed } = selectionPayload();
  if (!tiles.length && !typed) return;
  const token = ++suggestToken;
  const t0 = performance.now();
  try {
    const data = await thinking(() =>
      postJSON("/api/suggest", {
        history: state.history,
        tiles,
        typed,
        lang: state.lang,
        partner_lang: partnerLang(),
        clarification,
      })
    );
    if (token !== suggestToken) return; // a newer request superseded this one
    debug.note("suggest", `suggest ${Math.round(performance.now() - t0)} ms${data.fallback ? " (fallback)" : ""}`);
    console.log(
      `[suggest] ${Math.round(performance.now() - t0)} ms (llm ${data.ms} ms)` +
        (data.fallback ? " FALLBACK" : "")
    );
    if (data.clarify) renderClarify(data.clarify);
    else renderSentences(data);
  } catch (err) {
    console.warn("[suggest] failed:", err.message);
    toast(err.message, 4000);
  }
}

// After the partner speaks: replies that answer them (no tiles).
// Separate token, so a partner line never cancels the user's own pending Suggest.
let replyToken = 0;

async function suggestReplies() {
  const token = ++replyToken;
  const data = await thinking(() =>
    postJSON("/api/suggest", {
      history: state.history,
      tiles: [],
      typed: "",
      lang: state.lang,
      partner_lang: partnerLang(),
    })
  );
  if (token !== replyToken) return data;
  showMeaning(data.translation);
  if (data.quick_replies.length) {
    state.quickReplies = data.quick_replies;
    renderReplies(true);
  }
  return data;
}

// ======================================================================
// Rendering
// ======================================================================

function renderAll() {
  applyUI();
  renderLangPicker();
  renderPartnerPicker();
  renderReplies();
  renderTabs();
  renderGrid();
  renderUrgent();
  renderChips();
  renderRecent();
  renderSegments();
}

function renderLangPicker() {
  $("lang-picker").replaceChildren(
    ...state.languages.map((l) => {
      const o = el("option", null, l.name);
      o.value = l.id;
      o.selected = l.id === state.lang;
      return o;
    })
  );
}

// Hard rule 6: every suggestion row also offers "Something else" and "Wait".
function escapeButtons(onSomethingElse) {
  const out = [];
  const se = state.byId.something_else;
  const wait = state.byId.wait;
  if (se) out.push(button("reply escape", tileFace(se), onSomethingElse, "something_else"));
  if (wait) out.push(button("reply escape", tileFace(wait), onWait, "wait"));
  return out;
}

function focusBoard() {
  $("tabs").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderReplies(fresh = false) {
  const buttons = state.quickReplies.map((key) => {
    const tile = state.byId[key];
    const chipKey = tile ? key : TXT + key;
    const face = tile ? tileFace(tile) : [el("span", "label", key)];
    const selected = state.selected.includes(chipKey);
    const b = button("reply" + (selected ? " selected" : ""), face, () => {
      toggleSelected(chipKey);
      renderReplies();
    }, `reply:${chipKey}`);
    if (tile) b.dataset.key = keyFor(tile.category);
    return b;
  });
  const row = $("reply-row");
  row.replaceChildren(...buttons, ...escapeButtons(focusBoard));
  if (fresh) {
    row.classList.remove("flash");
    void row.offsetWidth; // restart the animation
    row.classList.add("flash");
  }
}

function renderTabs() {
  $("tabs").replaceChildren(
    ...state.categories.map((cat) => {
      const b = button("tab", (state.ui.categories && state.ui.categories[cat]) || cat, () => {
        state.activeCat = cat;
        renderTabs();
        renderGrid();
      });
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(cat === state.activeCat));
      b.dataset.key = keyFor(cat);
      return b;
    })
  );
}

function renderGrid() {
  const tiles = state.tiles.filter((t) => t.category === state.activeCat && !t.urgent);
  const grid = $("grid");
  grid.classList.toggle("editing", state.editMode);
  grid.replaceChildren(
    ...tiles.map((tile) => {
      const selected = state.selected.includes(tile.id);
      const face = tileFace(tile);
      if (state.editMode) face.push(el("span", "tile-x", "✕"));
      const b = button("tile" + (selected ? " selected" : ""), face, () => {
        if (state.editMode) return hideTile(tile);
        toggleSelected(tile.id);
        renderReplies();
      }, `tile:${tile.id}`);
      b.dataset.key = keyFor(tile.category);
      b.setAttribute("aria-pressed", String(selected));
      b.title = tile.label_en;
      return b;
    })
  );
}

function renderUrgent() {
  $("urgent-tiles").replaceChildren(
    ...state.tiles
      .filter((t) => t.urgent)
      .map((tile) => button("urgent-tile", tileFace(tile), () => onUrgent(tile), `urgent:${tile.id}`))
  );
}

function renderChips() {
  const box = $("chips");
  updateSuggestButton();
  if (state.selected.length === 0) {
    box.replaceChildren(el("span", "chips-empty", t("chips_empty")));
    return;
  }
  box.replaceChildren(
    ...state.selected.map((key) => {
      const info = chipInfo(key) || { emoji: "", label: key };
      const b = button(
        "chip",
        [el("span", "emoji", info.emoji), el("span", null, info.label), el("span", "x", "✕")],
        () => {
          toggleSelected(key);
          renderReplies();
        },
        `chip:${key}`
      );
      if (info.category) b.dataset.key = keyFor(info.category);
      b.setAttribute("aria-label", `Remove ${info.label}`);
      return b;
    })
  );
}

function updateSuggestButton() {
  $("suggest-btn").disabled = state.selected.length === 0 && !typedText();
}

function somethingElseFromResults() {
  $("results").replaceChildren();
  focusBoard();
}

// Sentence cards: text + Speak. Nothing is spoken until Speak is tapped (hard rule 1).
// When the partner speaks another language, each card shows what will be said in theirs.
function renderSentences({ sentences, spoken = [], spoken_lang: spokenLang, fallback: isFallback }) {
  const lang = state.lang;
  const cards = sentences.map((text, i) => {
    const say = spokenLang && spoken[i] ? { text: spoken[i], lang: spokenLang } : { text, lang };
    const speakBtn = button("btn btn-speak", [icon("speaker"), el("span", null, t("speak"))], () =>
      onSpeakCard(speakBtn, say.text, say.lang, text), `speak:${say.text}`);
    const showBtn = button("btn btn-show", [icon("expand"), el("span", null, t("show"))], () =>
      showToPartner(say.text, say.text !== text ? text : ""), `show:${say.text}`);
    const body = [el("p", "card-text", text)];
    if (say.lang !== lang) {
      body.push(el("p", "card-spoken", [`${t("will_say")} (${langName(say.lang)}): `, el("strong", null, say.text)]));
    }
    return el("div", "card", [el("div", "card-body", body), el("div", "card-actions", [speakBtn, showBtn])]);
  });
  const children = [];
  if (isFallback) children.push(el("p", "note", t("fallback_note")));
  children.push(el("div", "cards", cards));
  children.push(el("div", "row-escape", escapeButtons(somethingElseFromResults)));
  $("results").replaceChildren(...children);
}

// Clarifying question: big option buttons; the answer goes back to /api/suggest.
function renderClarify({ question, options }) {
  const opts = options.map((opt) =>
    button("btn btn-option", opt, () => onSuggest({ question, answer: opt }), `opt:${opt}`)
  );
  $("results").replaceChildren(
    el("div", "clarify", [
      el("p", "clarify-q", question),
      el("div", "clarify-options", [...opts, ...escapeButtons(somethingElseFromResults)]),
    ])
  );
}

async function onSpeakCard(btn, text, lang, shownText = text) {
  if (btn.classList.contains("playing")) {
    stopSpeaking();
    return;
  }
  const label = (b, playing) => b.replaceChildren(icon(playing ? "stop" : "speaker"), el("span", null, t(playing ? "stop" : "speak")));
  document.querySelectorAll(".btn-speak.playing").forEach((b) => {
    b.classList.remove("playing");
    label(b, false);
  });
  btn.classList.add("playing");
  label(btn, true);
  try {
    await speak(text, lang);
    logSpoken(shownText);
    remember(shownText, text, lang);
  } catch (err) {
    console.warn("[speak] failed:", err.message);
    toast(err.message, 4000);
  } finally {
    btn.classList.remove("playing");
    label(btn, false);
  }
}

// ---------- Recently said: one tap repeats a sentence the user already chose ----------

function remember(shown, text, lang) {
  state.recent = [{ shown, text, lang }, ...state.recent.filter((r) => r.text !== text)].slice(0, 5);
  saveSettings();
  renderRecent();
}

function renderRecent() {
  const box = $("recent");
  box.hidden = state.recent.length === 0;
  box.replaceChildren(
    el("span", "recent-label", t("recent")),
    ...state.recent.map((r) =>
      button("recent-item", [icon("repeat"), el("span", null, r.shown)], () => {
        speak(r.text, r.lang).catch((err) => toast(err.message, 4000));
      }, `recent:${r.text}`)
    )
  );
}

// ---------- Show to partner: big text for noise or a partner who is hard of hearing ----------

function showToPartner(text, sub) {
  $("show-overlay-text").textContent = text;
  $("show-overlay-sub").textContent = sub || "";
  $("show-overlay").hidden = false;
}

// ======================================================================
// Listening: mic -> voice-activity detection -> WAV -> /api/listen -> /api/suggest
// Prisma doesn't accept WebM, so we capture raw PCM and encode 16 kHz WAV ourselves.
// ======================================================================

const VAD = {
  listen: { minRms: 0.012, factor: 3.0 },
  phone: { minRms: 0.008, factor: 2.5 }, // speakerphone audio is quieter
  startMs: 90,       // this much loud audio starts a segment
  silenceMs: 1200,   // this much quiet ends it
  maxMs: 25000,      // hard cap (Prisma takes up to 30 s)
  preRollMs: 300,    // keep audio from just before the start
  minVoicedMs: 350,  // shorter segments are noise (a cough, a door)
  targetRate: 16000,
};

const WORKLET_SRC = `
class Tap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(1024); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("tap", Tap);`;

const mic = {
  on: false,
  recording: false,
  ctx: null,
  stream: null,
  node: null,
  rate: 48000,
  floor: 0.005,
  loudMs: 0,
  preRoll: [],
  chunks: [],
  voicedMs: 0,
  quietMs: 0,
  segMs: 0,
  queue: Promise.resolve(),

  async start() {
    if (!navigator.mediaDevices?.getUserMedia) {
      toast("This browser can't use the mic here. Use https or localhost.", 5000);
      return;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      toast(`Microphone blocked: ${err.message}`, 5000);
      return;
    }
    try {
      this.ctx = new AudioContext();
      await this.ctx.resume(); // Safari starts it suspended after the await above
      this.rate = this.ctx.sampleRate;
      const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: "text/javascript" }));
      await this.ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      const src = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "tap");
      this.node.port.onmessage = (e) => this.onFrame(e.data);
      src.connect(this.node);
      this.node.connect(this.ctx.destination); // outputs silence; keeps the node running
    } catch (err) {
      console.error("[listen] audio setup failed:", err);
      toast(`Could not start listening: ${err.message}`, 5000);
      this.stop();
      return;
    }
    this.on = true;
    this.reset();
    renderMicButton();
    refreshStatus();
    console.log(`[listen] mic on, ${this.rate} Hz, mode=${state.mode}`);
  },

  stop() {
    this.on = false;
    this.reset();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close();
    this.ctx = this.stream = this.node = null;
    renderMicButton();
    refreshStatus();
    console.log("[listen] mic off");
  },

  reset() {
    this.recording = false;
    this.loudMs = this.voicedMs = this.quietMs = this.segMs = 0;
    this.preRoll = [];
    this.chunks = [];
  },

  abortSegment() {
    if (this.recording) console.log("[listen] segment dropped: we started speaking");
    this.reset();
    refreshStatus();
  },

  onFrame(frame) {
    if (!this.on) return;
    if (state.speaking) return; // half-duplex: ignore everything while we talk (+300 ms)

    let sum = 0;
    for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    const rms = Math.sqrt(sum / frame.length);
    const ms = (frame.length / this.rate) * 1000;
    const cfg = VAD[state.mode] || VAD.listen;
    const threshold = Math.max(cfg.minRms, this.floor * cfg.factor);
    debug.meter(rms, threshold);

    if (!this.recording) {
      this.preRoll.push(frame);
      while (this.preRoll.length * ms > VAD.preRollMs) this.preRoll.shift();
      if (rms > threshold) {
        this.loudMs += ms;
        if (this.loudMs >= VAD.startMs) {
          this.recording = true;
          this.chunks = [...this.preRoll];
          this.preRoll = [];
          this.segMs = this.chunks.length * ms;
          this.voicedMs = this.loudMs;
          this.quietMs = 0;
          refreshStatus();
        }
      } else {
        this.loudMs = 0;
        this.floor = this.floor * 0.95 + rms * 0.05; // track background noise
      }
      return;
    }

    this.chunks.push(frame);
    this.segMs += ms;
    if (rms > threshold * 0.6) {
      this.voicedMs += ms;
      this.quietMs = 0;
    } else {
      this.quietMs += ms;
    }
    if (this.quietMs >= VAD.silenceMs || this.segMs >= VAD.maxMs) this.finishSegment();
  },

  finishSegment() {
    const chunks = this.chunks;
    const voiced = this.voicedMs;
    const total = this.segMs;
    const rate = this.rate;
    this.reset();
    refreshStatus();
    if (voiced < VAD.minVoicedMs) {
      console.log(`[listen] ignored ${Math.round(voiced)} ms blip`);
      return;
    }
    const endedAt = performance.now();
    const wav = encodeWav(chunks, rate, VAD.targetRate);
    console.log(`[listen] segment ${Math.round(total)} ms (${Math.round(voiced)} ms voiced)`);
    // Process segments one at a time, in order.
    this.queue = this.queue.then(() => handleSegment(wav, endedAt)).catch(() => {});
  },
};

function encodeWav(chunks, inRate, outRate) {
  const length = chunks.reduce((n, c) => n + c.length, 0);
  const input = new Float32Array(length);
  let o = 0;
  for (const c of chunks) {
    input.set(c, o);
    o += c.length;
  }
  // Resample by averaging the input samples that fall into each output sample.
  const ratio = inRate / outRate;
  const outLen = Math.floor(length / ratio);
  const pcm = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(length, Math.floor((i + 1) * ratio));
    let s = 0;
    for (let j = start; j < end; j++) s += input[j];
    const v = Math.max(-1, Math.min(1, s / Math.max(1, end - start)));
    pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const dv = new DataView(buf);
  const str = (off, s) => [...s].forEach((ch, i) => dv.setUint8(off + i, ch.charCodeAt(0)));
  str(0, "RIFF");
  dv.setUint32(4, 36 + pcm.length * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true); // PCM
  dv.setUint16(22, 1, true); // mono
  dv.setUint32(24, outRate, true);
  dv.setUint32(28, outRate * 2, true);
  dv.setUint16(32, 2, true);
  dv.setUint16(34, 16, true);
  str(36, "data");
  dv.setUint32(40, pcm.length * 2, true);
  new Int16Array(buf, 44).set(pcm);
  return new Blob([buf], { type: "audio/wav" });
}

async function handleSegment(wav, endedAt) {
  const form = new FormData();
  form.append("audio", wav, "partner.wav");
  form.append("lang", state.lang);
  form.append("partner", state.partnerSetting);

  let heard;
  try {
    heard = await thinking(async () => {
      const res = await fetch("/api/listen", { method: "POST", body: form });
      if (!res.ok) throw new Error(await errorText(res));
      return res.json();
    });
  } catch (err) {
    console.warn("[listen] failed:", err.message);
    toast(`Listening failed: ${err.message}`, 4000);
    return;
  }
  const sttMs = Math.round(performance.now() - endedAt);
  if (!heard.transcript) {
    console.log(`[listen] no speech found (${sttMs} ms)`);
    return;
  }

  if (state.partnerSetting === "auto") state.partnerDetected = heard.partner_lang;
  debug.note("heard", `heard (${heard.language}): "${heard.transcript}"`);
  showPartnerLine(heard.transcript);

  try {
    const data = await suggestReplies();
    const total = Math.round(performance.now() - endedAt);
    debug.note("timing", `stt ${sttMs} ms + replies ${total - sttMs} ms = ${total} ms ${total < 3000 ? "✓" : "✗"}`);
    console.log(
      `[listen] "${heard.transcript}" (${heard.language}) | stt ${sttMs} ms | suggest ${total - sttMs} ms | ` +
        `total ${total} ms ${total < 3000 ? "✓" : "✗ over 3 s target"}` +
        (data && !data.quick_replies.length ? " | no new replies (kept row)" : "")
    );
  } catch (err) {
    console.warn("[listen] suggest failed:", err.message);
  }
}

function showPartnerLine(text) {
  state.history = [...state.history, text].slice(-MAX_HISTORY);
  $("partner-text").textContent = text;
  $("partner-text").title = text;
  showMeaning(""); // the translation arrives with the reply suggestions
  learnSoon(); // the server logged this line for personal-board learning
}

function renderMicButton() {
  const b = $("mic-btn");
  b.replaceChildren(icon(mic.on ? "pause" : "mic"), el("span", "mic-label", mic.on ? t("stop_listening") : t("start_listening")));
  b.setAttribute("aria-pressed", String(mic.on));
  b.classList.toggle("on", mic.on);
}

// ======================================================================
// Personal board learning: the AI proposes new tiles from this person's
// conversations; the user decides. Tiles never move by themselves.
// ======================================================================

let learnTimer = null;

// Ask for tile ideas a little after activity settles, so it never competes with live replies.
// Only partner lines trigger this; the server calls the AI after every 4 new partner lines.
function learnSoon() {
  clearTimeout(learnTimer);
  learnTimer = setTimeout(proposeIdeas, 15000);
}

function logSpoken(text) {
  fetch("/api/learn/spoke", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  }).catch(() => {});
}

async function loadIdeas() {
  try {
    const res = await fetch(`/api/learn?lang=${encodeURIComponent(state.lang)}`);
    if (res.ok) showIdeas((await res.json()).pending);
  } catch {}
}

async function proposeIdeas() {
  try {
    const t0 = performance.now();
    const data = await postJSON(`/api/learn/propose?lang=${encodeURIComponent(state.lang)}`, {});
    debug.note("learn", `ideas ${data.pending.length} (${Math.round(performance.now() - t0)} ms)`);
    showIdeas(data.pending);
  } catch (err) {
    console.warn("[learn] failed:", err.message);
  }
}

function showIdeas(ideas) {
  const fresh = ideas.some((i) => !state.ideas.find((o) => o.id === i.id));
  state.ideas = ideas;
  const box = $("ideas");
  box.hidden = ideas.length === 0;
  if (!ideas.length) return box.replaceChildren();
  box.replaceChildren(
    el("span", "ideas-title", t("ideas_title")),
    ...ideas.map((idea) =>
      el("div", "idea", [
        el("span", "emoji", idea.emoji),
        el("span", "idea-text", [el("strong", null, idea.label), el("small", null, idea.reason)]),
        button("idea-add", [icon("plus"), el("span", null, t("add"))], () => decideIdea(idea, true), `idea+:${idea.id}`),
        button("idea-no", icon("x"), () => decideIdea(idea, false), `idea-:${idea.id}`),
      ])
    )
  );
  if (fresh) {
    box.classList.remove("flash");
    void box.offsetWidth;
    box.classList.add("flash");
  }
}

async function decideIdea(idea, accept) {
  try {
    await postJSON(accept ? "/api/learn/accept" : "/api/learn/reject", { id: idea.id });
  } catch (err) {
    return toast(err.message);
  }
  showIdeas(state.ideas.filter((i) => i.id !== idea.id));
  if (accept) {
    await loadSymbols(state.lang);
    state.activeCat = state.categories[0]; // "My tiles"
    renderTabs();
    renderGrid();
    toast(`${idea.emoji} ${idea.label}: ${t("added")}`);
  }
}

// ---------- Edit mode: hide tiles this person never uses ----------

function setEditMode(on) {
  state.editMode = on;
  $("edit-bar").hidden = !on;
  if (on) closeSettings();
  $("unhide-btn").hidden = !on || state.hiddenCount === 0;
  $("unhide-btn").textContent = `${t("show_hidden")} (${state.hiddenCount})`;
  renderGrid();
}

async function hideTile(tile) {
  if (tile.learned && !confirm(`${tile.emoji} ${tile.label}: ${t("remove_confirm")}`)) return;
  try {
    await postJSON("/api/tiles/hide", { id: tile.id });
  } catch (err) {
    return toast(err.message);
  }
  await loadSymbols(state.lang);
  setEditMode(true);
}

async function unhideAll() {
  await postJSON("/api/tiles/unhide", {}).catch((err) => toast(err.message));
  await loadSymbols(state.lang);
  setEditMode(true);
}

async function forgetConversations() {
  if (!confirm(t("forget_confirm"))) return;
  await postJSON("/api/learn/forget", {}).catch((err) => toast(err.message));
  showIdeas([]);
  toast(t("forgotten"));
}

// ======================================================================
// Languages: interface text, partner language, translation
// ======================================================================

function applyUI() {
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    node.placeholder = t(node.dataset.i18nPlaceholder);
  });
  renderMicButton();
  refreshStatus();
  $("scan-hint").textContent = t("scan_choose");
  if (typeof voice !== "undefined") voice.render();
}

function renderPartnerPicker() {
  const option = (value, label) => {
    const o = el("option", null, label);
    o.value = value;
    o.selected = value === state.partnerSetting;
    return o;
  };
  $("partner-picker").replaceChildren(
    option("", t("same_as_me")),
    option("auto", t("auto_detect")),
    ...state.partnerLanguages.map((l) => option(l.id, l.name))
  );
}

function setPartnerSetting(value) {
  state.partnerSetting = value;
  settings.partner = value;
  saveSettings();
  state.partnerDetected = null;
  showMeaning("");
  prefetchFixed(); // urgent phrases in the partner's language, if available
  console.log(`[lang] partner language: ${value || "same as mine"}`);
}

function showMeaning(text) {
  $("partner-meaning").textContent = text || "";
  $("partner-meaning-line").hidden = !text;
  if (text) debug.note("heard", `${debug.lines.heard || ""} → "${text}"`);
}

// ======================================================================
// Settings drawer
// ======================================================================

function openSettings() {
  $("settings").hidden = false;
  scan.pause(true);
}

function closeSettings() {
  $("settings").hidden = true;
  scan.pause(false);
}

function renderSegments() {
  const mark = (groupId, attr, value) =>
    document.querySelectorAll(`#${groupId} button`).forEach((b) => b.classList.toggle("active", b.dataset[attr] === String(value)));
  mark("size-group", "size", settings.size);
  mark("guard-group", "guard", settings.guard);
  mark("scan-group", "scan", settings.scan);
}

function applySettings() {
  document.body.dataset.size = settings.size;
  renderSegments();
  scan.setInterval(Number(settings.scan) || 0);
}

function wireSegments() {
  const wire = (groupId, attr, apply) =>
    document.querySelectorAll(`#${groupId} button`).forEach((b) =>
      b.addEventListener("click", () => {
        apply(b.dataset[attr]);
        saveSettings();
        applySettings();
      })
    );
  wire("size-group", "size", (v) => (settings.size = v));
  wire("guard-group", "guard", (v) => (settings.guard = Number(v)));
  wire("scan-group", "scan", (v) => (settings.scan = Number(v)));
}

// ======================================================================
// Switch scanning: groups light up one by one, then the buttons inside.
// One switch (Space/Enter, or a tap anywhere) chooses. Many people with
// severe CP use AAC this way instead of pointing.
// ======================================================================

const scan = {
  interval: 0,
  timer: null,
  level: "group",   // "group" | "item"
  groups: [],
  gi: -1,
  items: [],
  ii: -1,
  rounds: 0,
  paused: false,
  clicking: false,

  groupDefs() {
    const visible = (node) => node && !node.hidden && node.offsetParent !== null;
    const buttons = (sel) => [...document.querySelectorAll(sel)].filter((b) => visible(b) && !b.disabled);
    return [
      { name: "replies", el: $("reply-row"), items: () => buttons("#reply-row button, #recent button") },
      { name: "results", el: $("results"), items: () => buttons("#results button") },
      { name: "tabs", el: $("tabs"), items: () => buttons("#tabs button") },
      { name: "tiles", el: $("grid"), items: () => buttons("#grid button, #ideas button") },
      { name: "message", el: document.querySelector(".compose"), items: () => buttons(".compose button") },
      { name: "urgent", el: document.querySelector(".urgent"), items: () => buttons(".urgent button") },
    ].filter((g) => visible(g.el) && g.items().length);
  },

  setInterval(ms) {
    this.interval = ms;
    this.stopTimer();
    this.clear();
    $("scan-hint").hidden = !ms;
    if (ms) {
      $("scan-hint").textContent = t("scan_choose");
      this.level = "group";
      this.gi = -1;
      this.timer = setInterval(() => this.step(), ms);
      this.step();
    }
  },

  stopTimer() {
    clearInterval(this.timer);
    this.timer = null;
  },

  pause(on) {
    this.paused = on;
  },

  clear() {
    document.querySelectorAll(".scan-group, .scan-item").forEach((n) => n.classList.remove("scan-group", "scan-item"));
  },

  step() {
    if (this.paused || !$("urgent-overlay").hidden || !$("show-overlay").hidden) return;
    this.clear();
    if (this.level === "group") {
      this.groups = this.groupDefs();
      if (!this.groups.length) return;
      this.gi = (this.gi + 1) % this.groups.length;
      this.groups[this.gi].el.classList.add("scan-group");
    } else {
      this.items = this.groups[this.gi]?.items() || [];
      if (!this.items.length) return this.backToGroups();
      this.ii += 1;
      if (this.ii >= this.items.length) {
        this.ii = 0;
        if (++this.rounds >= 2) return this.backToGroups(); // nothing chosen twice round: go back up
      }
      const item = this.items[this.ii];
      item.classList.add("scan-item");
      item.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  },

  backToGroups() {
    this.level = "group";
    this.clear();
    this.step();
  },

  select() {
    if (!this.interval) return;
    if (this.level === "group") {
      if (this.gi < 0 || !this.groups[this.gi]) return;
      this.level = "item";
      this.ii = -1;
      this.rounds = 0;
    } else {
      const item = this.items[this.ii];
      this.level = "group";
      this.gi = -1;
      if (item) {
        this.clicking = true;
        item.click();
        this.clicking = false;
      }
    }
    // Restart the timer so the next highlight gets a full interval.
    this.stopTimer();
    this.timer = setInterval(() => this.step(), this.interval);
    this.step();
  },

  init() {
    const exempt = (target) => target.closest?.("#settings, #settings-btn, .overlay");
    // In scanning mode a tap anywhere is the switch; normal pointing is disabled.
    document.addEventListener("pointerdown", (e) => {
      if (!this.interval || this.paused || exempt(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
      this.select();
    }, true);
    document.addEventListener("click", (e) => {
      if (!this.interval || this.paused || this.clicking || exempt(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
    }, true);
    document.addEventListener("keydown", (e) => {
      if (!this.interval || this.paused || (e.key !== " " && e.key !== "Enter")) return;
      if (e.target.matches?.("input, select, textarea")) return; // target may be the document itself
      e.preventDefault();
      this.select();
    });
  },
};

// ======================================================================
// My voice: clone a voice from 5-30 s of audio (Gnani voice cloning).
// Only the voice embedding is kept on this device; the recording is not saved.
// ======================================================================

const voice = {
  sample: null,      // Blob ready to upload
  sampleMs: 0,
  rec: null,         // {ctx, stream, node, chunks, rate, started, timer}
  status: { cloned: false, enabled: false },

  async load() {
    try {
      const res = await fetch("/api/voice");
      if (res.ok) this.status = await res.json();
    } catch {}
    this.render();
  },

  render() {
    const st = this.status;
    $("voice-status").textContent = st.cloned && st.enabled ? t("voice_using_cloned") : t("voice_using_standard");
    $("voice-use").hidden = !st.cloned;
    $("voice-delete").hidden = !st.cloned;
    document.querySelectorAll("#voice-use button").forEach((b) =>
      b.classList.toggle("active", (b.dataset.use === "1") === !!st.enabled));
    const recording = !!this.rec;
    $("voice-record").classList.toggle("recording", recording);
    $("voice-record-label").textContent = recording ? `${t("stop_recording")} · ${Math.floor((performance.now() - this.rec.started) / 1000)} s` : t("record_sample");
    $("voice-sample").hidden = !this.sample;
    if (this.sample) $("voice-sample").textContent = `✓ ${(this.sampleMs / 1000).toFixed(0)} s`;
    $("voice-create").disabled = !this.sample || !$("voice-consent").checked;
  },

  async startRecording() {
    if (mic.on) mic.stop(); // one microphone user at a time
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, noiseSuppression: true } });
      const ctx = new AudioContext();
      await ctx.resume();
      const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: "text/javascript" }));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      const node = new AudioWorkletNode(ctx, "tap");
      const chunks = [];
      node.port.onmessage = (e) => chunks.push(e.data);
      ctx.createMediaStreamSource(stream).connect(node);
      node.connect(ctx.destination);
      this.rec = { ctx, stream, node, chunks, rate: ctx.sampleRate, started: performance.now() };
      this.rec.timer = setInterval(() => {
        this.render();
        if (performance.now() - this.rec.started >= 30000) this.stopRecording(); // Gnani takes up to 30 s
      }, 250);
      this.sample = null;
      this.render();
    } catch (err) {
      toast(`Microphone: ${err.message}`, 4000);
    }
  },

  stopRecording() {
    const r = this.rec;
    if (!r) return;
    clearInterval(r.timer);
    r.node.disconnect();
    r.stream.getTracks().forEach((tr) => tr.stop());
    r.ctx.close();
    this.rec = null;
    const ms = performance.now() - r.started;
    if (ms < 5000) {
      toast(t("voice_too_short"));
    } else {
      this.sample = encodeWav(r.chunks, r.rate, 16000);
      this.sampleMs = ms;
    }
    this.render();
  },

  async create() {
    if (!this.sample || !$("voice-consent").checked) return;
    const form = new FormData();
    form.append("audio", this.sample, this.sample.name || "voice.wav");
    form.append("consent", "true");
    toast(t("voice_working"), 10000);
    try {
      const res = await fetch("/api/voice/clone", { method: "POST", body: form });
      if (!res.ok) throw new Error(await errorText(res));
      this.status = await res.json();
      this.sample = null;
      toast(t("voice_created"));
      this.voiceChanged();
    } catch (err) {
      toast(err.message, 5000);
    }
    this.render();
  },

  async use(enabled) {
    try {
      this.status = await postJSON("/api/voice/use", { enabled });
      this.voiceChanged();
    } catch (err) {
      toast(err.message);
    }
    this.render();
  },

  async remove() {
    if (!confirm(`${t("delete_voice")}?`)) return;
    this.status = await postJSON("/api/voice/delete", {}).catch(() => this.status);
    toast(t("voice_deleted"));
    this.voiceChanged();
    this.render();
  },

  // Audio cached in the browser was made with the old voice.
  voiceChanged() {
    for (const p of audioCache.values()) p.then((u) => URL.revokeObjectURL(u)).catch(() => {});
    audioCache.clear();
    prefetchFixed();
  },

  init() {
    $("voice-record").addEventListener("click", () => (this.rec ? this.stopRecording() : this.startRecording()));
    $("voice-file").addEventListener("change", (e) => {
      const f = e.target.files[0];
      if (!f) return;
      this.sample = f;
      this.sampleMs = 0;
      this.render();
      $("voice-sample").textContent = `✓ ${f.name}`;
    });
    $("voice-consent").addEventListener("change", () => this.render());
    $("voice-create").addEventListener("click", () => this.create());
    document.querySelectorAll("#voice-use button").forEach((b) =>
      b.addEventListener("click", () => this.use(b.dataset.use === "1")));
    $("voice-delete").addEventListener("click", () => this.remove());
    this.load();
  },
};

// ======================================================================
// Demo mode (?demo=1): type what the partner said instead of speaking it,
// and load a synthetic week of conversation to show board learning.
// ======================================================================

const DEMO_LINES = [
  "Aarav, khana khaoge? Dal chawal hai, ya khichdi bana doon?",
  "Theek hai. Saath mein dahi bhi doon?",
  "Aur kal doctor ke yahan chalna hai, yaad hai na?",
  "ನಮಸ್ಕಾರ, ಶ್ರೀ ಸಾಯಿ ಮೆಡಿಕಲ್ಸ್. ಹೇಳಿ ಸರ್, ಏನು ಬೇಕು?",
  "500 mg ಆ, 650 ಆ?",
  "ಸರಿ. ಹೋಮ್ ಡೆಲಿವರಿ ಬೇಕಾ? ಅಡ್ರೆಸ್ ಹೇಳಿ.",
];

async function demoPartnerSays(text) {
  text = text.trim();
  if (!text) return;
  const t0 = performance.now();
  try {
    await postJSON("/api/demo/partner", { text });
  } catch {}
  debug.note("heard", `typed: "${text}"`);
  showPartnerLine(text);
  const data = await suggestReplies().catch((err) => toast(err.message));
  debug.note("timing", `replies ${Math.round(performance.now() - t0)} ms`);
  return data;
}

function initDemo() {
  if (!new URLSearchParams(location.search).has("demo")) return;
  $("demo-panel").hidden = false;
  $("demo-lines").replaceChildren(
    ...DEMO_LINES.map((line) => {
      const b = el("button", "demo-line", line.length > 34 ? line.slice(0, 32) + "…" : line);
      b.type = "button";
      b.title = line;
      b.addEventListener("click", () => demoPartnerSays(line));
      return b;
    })
  );
  $("demo-input").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    demoPartnerSays(e.target.value);
    e.target.value = "";
  });
  $("demo-week").addEventListener("click", async () => {
    try {
      const data = await thinking(() => postJSON(`/api/demo/seed?lang=${encodeURIComponent(state.lang)}`, {}));
      showIdeas(data.pending);
      if (!data.pending.length) toast("No new ideas (already added or rejected?)");
    } catch (err) {
      toast(err.message, 4000);
    }
  });
}

// ======================================================================
// Wiring
// ======================================================================

function setMode(mode) {
  state.mode = mode;
  settings.mode = mode;
  saveSettings();
  document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $("phone-banner").hidden = mode !== "phone";
  if (mic.on) console.log(`[listen] mode=${mode}`);
}

function init() {
  loadSettings();
  state.partnerSetting = settings.partner || "";
  $("partner-picker").addEventListener("change", (e) => setPartnerSetting(e.target.value));
  $("settings-btn").addEventListener("click", openSettings);
  document.querySelectorAll("[data-close]").forEach((n) => n.addEventListener("click", closeSettings));
  $("show-overlay").addEventListener("click", () => ($("show-overlay").hidden = true));
  window.addEventListener("online", () => setOffline(false));
  window.addEventListener("offline", () => setOffline(true));
  setOffline(!navigator.onLine);
  wireSegments();
  scan.init();
  initDemo();
  voice.init();
  $("lang-picker").addEventListener("change", (e) => {
    loadSymbols(e.target.value).catch((err) => toast(err.message));
  });
  document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  $("mic-btn").addEventListener("click", () => (mic.on ? mic.stop() : mic.start()));
  $("clear-btn").addEventListener("click", () => {
    clearMessage();
    renderReplies();
  });
  $("suggest-btn").addEventListener("click", () => onSuggest());
  $("typed").addEventListener("input", updateSuggestButton);
  $("typed").addEventListener("keydown", (e) => {
    if (e.key === "Enter") onSuggest();
  });
  $("urgent-overlay").addEventListener("click", () => ($("urgent-overlay").hidden = true));
  $("edit-btn").addEventListener("click", () => setEditMode(true));
  $("edit-done").addEventListener("click", () => setEditMode(false));
  $("unhide-btn").addEventListener("click", unhideAll);
  $("forget-btn").addEventListener("click", forgetConversations);
  debug.init();
  setMode(settings.mode === "phone" ? "phone" : "listen");
  applySettings();

  refreshStatus();
  loadSymbols().catch((err) => {
    console.error(err);
    toast("Could not load the board. Is the server running?");
  });
}

init();

// Installable app: the page and the board keep loading without internet.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch((err) => console.warn("[pwa] not registered:", err.message));
}
