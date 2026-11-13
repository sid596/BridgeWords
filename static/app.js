// BRIDGEWORDS frontend (vanilla JS, no build step).
// Board + chips, speech via /api/speak, suggestions via /api/suggest,
// listening via mic + voice-activity detection + /api/listen, phone mode.
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
};

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

function button(className, children, onClick) {
  const b = el("button", className, children);
  b.type = "button";
  b.addEventListener("click", onClick);
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
  $("status").textContent = s;
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

function langCode(lang) {
  return state.languages.find((l) => l.id === lang)?.code || "en-IN";
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
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
async function speakFixed(tile) {
  const text = tile.say || tile.label;
  const lang = state.lang;
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

async function prefetchFixed() {
  const lang = state.lang;
  const fixed = state.tiles.filter((t) => t.say);
  const box = $("urgent-state");
  box.textContent = "loading voice…";
  const results = await Promise.allSettled(fixed.map((t) => fetchSpeech(t.say, lang)));
  if (lang !== state.lang) return; // language changed meanwhile
  const failed = results.find((r) => r.status === "rejected");
  box.textContent = failed ? "⚠ device voice" : "🔊 voice ready";
  box.title = failed ? failed.reason.message : "";
  if (failed) console.warn("[urgent] voice not cached:", failed.reason.message);
}

function onUrgent(tile) {
  console.log("[urgent]", tile.id);
  $("urgent-overlay-emoji").textContent = tile.emoji;
  $("urgent-overlay-text").textContent = tile.say || tile.label;
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
  state.categories = data.categories;
  state.tiles = data.tiles;
  state.byId = Object.fromEntries(data.tiles.map((t) => [t.id, t]));
  if (!state.categories.includes(state.activeCat)) state.activeCat = state.categories[0];
  document.documentElement.lang = data.lang === "hinglish" ? "hi-Latn" : data.lang;

  renderAll();
  prefetchFixed();
}

function chipInfo(key) {
  if (key.startsWith(TXT)) return { emoji: "💬", label: key.slice(TXT.length) };
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
        clarification,
      })
    );
    if (token !== suggestToken) return; // a newer request superseded this one
    console.log(
      `[suggest] ${Math.round(performance.now() - t0)} ms (llm ${data.ms} ms)` +
        (data.fallback ? " FALLBACK" : "")
    );
    if (data.clarify) renderClarify(data.clarify);
    else renderSentences(data.sentences, data.fallback);
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
    postJSON("/api/suggest", { history: state.history, tiles: [], typed: "", lang: state.lang })
  );
  if (token !== replyToken) return data;
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
  renderLangPicker();
  renderReplies();
  renderTabs();
  renderGrid();
  renderUrgent();
  renderChips();
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
  if (se) out.push(button("reply escape", tileFace(se), onSomethingElse));
  if (wait) out.push(button("reply escape", tileFace(wait), onWait));
  return out;
}

function focusBoard() {
  $("tabs").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderReplies(fresh = false) {
  const buttons = state.quickReplies.map((key) => {
    const tile = state.byId[key];
    const chipKey = tile ? key : TXT + key;
    const face = tile ? tileFace(tile) : [el("span", "emoji", "💬"), el("span", "label", key)];
    const selected = state.selected.includes(chipKey);
    return button("reply" + (selected ? " selected" : ""), face, () => {
      toggleSelected(chipKey);
      renderReplies();
    });
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
      const b = button("tab", cat, () => {
        state.activeCat = cat;
        renderTabs();
        renderGrid();
      });
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(cat === state.activeCat));
      return b;
    })
  );
}

function renderGrid() {
  const tiles = state.tiles.filter((t) => t.category === state.activeCat && !t.urgent);
  $("grid").replaceChildren(
    ...tiles.map((tile) => {
      const selected = state.selected.includes(tile.id);
      const b = button("tile" + (selected ? " selected" : ""), tileFace(tile), () => {
        toggleSelected(tile.id);
        renderReplies();
      });
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
      .map((tile) => button("urgent-tile", tileFace(tile), () => onUrgent(tile)))
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
    ...state.selected.map((key) => {
      const info = chipInfo(key) || { emoji: "", label: key };
      const b = button(
        "chip",
        [el("span", "emoji", info.emoji), el("span", null, info.label), el("span", "x", "✕")],
        () => {
          toggleSelected(key);
          renderReplies();
        }
      );
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
function renderSentences(sentences, isFallback) {
  const lang = state.lang;
  const cards = sentences.map((text) => {
    const speakBtn = el("button", "btn btn-speak", "🔊 Speak");
    speakBtn.type = "button";
    speakBtn.addEventListener("click", () => onSpeakCard(speakBtn, text, lang));
    return el("div", "card", [el("p", "card-text", text), speakBtn]);
  });
  const children = [];
  if (isFallback) children.push(el("p", "note", "AI suggestions unavailable: your words as tapped."));
  children.push(el("div", "cards", cards));
  children.push(el("div", "row-escape", escapeButtons(somethingElseFromResults)));
  $("results").replaceChildren(...children);
}

// Clarifying question: big option buttons; the answer goes back to /api/suggest.
function renderClarify({ question, options }) {
  const opts = options.map((opt) =>
    button("btn btn-option", opt, () => onSuggest({ question, answer: opt }))
  );
  $("results").replaceChildren(
    el("div", "clarify", [
      el("p", "clarify-q", ["❓ ", question]),
      el("div", "clarify-options", [...opts, ...escapeButtons(somethingElseFromResults)]),
    ])
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

  state.history = [...state.history, heard.transcript].slice(-MAX_HISTORY);
  $("partner-text").textContent = heard.transcript;
  $("partner-text").title = heard.transcript;

  try {
    const data = await suggestReplies();
    const total = Math.round(performance.now() - endedAt);
    console.log(
      `[listen] "${heard.transcript}" (${heard.language}) | stt ${sttMs} ms | suggest ${total - sttMs} ms | ` +
        `total ${total} ms ${total < 3000 ? "✓" : "✗ over 3 s target"}` +
        (data && !data.quick_replies.length ? " | no new replies (kept row)" : "")
    );
  } catch (err) {
    console.warn("[listen] suggest failed:", err.message);
  }
}

function renderMicButton() {
  const b = $("mic-btn");
  b.textContent = mic.on ? "⏸ Stop listening" : "🎙 Start listening";
  b.setAttribute("aria-pressed", String(mic.on));
  b.classList.toggle("on", mic.on);
}

// ======================================================================
// Wiring
// ======================================================================

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $("phone-banner").hidden = mode !== "phone";
  if (mic.on) console.log(`[listen] mode=${mode}`);
}

function init() {
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

  refreshStatus();
  loadSymbols().catch((err) => {
    console.error(err);
    toast("Could not load the board. Is the server running?");
  });
}

init();
