// BRIDGEWORDS frontend. Step 1: board, tabs, chips, language picker.
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
};

const $ = (id) => document.getElementById(id);

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
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 2200);
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

function onUrgent(tile) {
  // Step 2 wires this to cached Timbre audio that plays immediately.
  console.log("[urgent]", tile.id);
  toast(`${tile.emoji} ${tile.label}: speaks instantly from step 2`);
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

  loadSymbols().catch((err) => {
    console.error(err);
    toast("Could not load the board. Is the server running?");
  });
}

init();
