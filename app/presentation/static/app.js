// Веб-застосунок D&D Adventures (п. 4.9). Розділи: «Пригоди» (пошук і добірки,
// групи запитів 1–3, 9), сторінки пригоди, монстра, автора й персонажа (групи 4–6, 8),
// «Бестіарій», «Хроніки» (наратив реальних боїв), «Зала слави» (статистика гри,
// група 7), «Атлас» (аналітика ніш map-reduce по шардах, група 11) і «Кузня» —
// моніторинг (таблиця 4.16). Дані — REST-маршрути таблиці 4.15
// і потік SSE /api/stats/stream, що раз на секунду надсилає знімок стану системи.
"use strict";

const $ = (id) => document.getElementById(id);
const NF = new Intl.NumberFormat("uk-UA");
const NF1 = new Intl.NumberFormat("uk-UA", { maximumFractionDigits: 1 });
const USD = new Intl.NumberFormat("uk-UA", { style: "currency", currency: "USD", currencyDisplay: "narrowSymbol" });
const fmt = (n) => (n == null || Number.isNaN(n) ? "—" : NF.format(Math.round(n)));
const fmt1 = (n) => (n == null || Number.isNaN(n) ? "—" : NF1.format(n));
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)} %`);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;

// для дробових чисел іменник стоїть у родовому відмінку однини: «2,5 години»
const unit = (n, one, few, many, fraction) => (Number.isInteger(n) ? plural(n, one, few, many) : fraction);
function plural(n, one, few, many) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

// ---------------------------------------------------------------- довідники
const ENVIRONMENTS = { arctic: "арктика", coastal: "узбережжя", desert: "пустеля", forest: "ліс", graveyard: "цвинтар", grassland: "степ",
  hill: "пагорби", mountain: "гори", swamp: "болото", underdark: "Підзем'я", underwater: "під водою", urban: "місто" };
const CREATURE_TYPES = { aberration: "аберація", beast: "звір", celestial: "небожитель", construct: "конструкт", dragon: "дракон",
  elemental: "елементаль", fey: "фея", fiend: "дух пекла", giant: "велетень", humanoid: "гуманоїд", monstrosity: "чудовисько",
  ooze: "слиз", plant: "рослина", undead: "нежить" };
const CLASSES = ["Artificer", "Barbarian", "Bard", "Blood Hunter", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard"];
const STAGE_LABELS = { lists: "сторінки списку", cards: "картки модулів", authors: "профілі авторів", tags: "сторінки тегів" };
const FILL_LABELS = { level_min: "рівень персонажів", players_min: "кількість гравців", duration_hours: "тривалість", price: "ціна",
  publication_language: "мова видання", published_year: "рік видання", synopsis: "синопсис", system: "система", genre: "жанр",
  rating_avg: "рейтинг", reviews: "відгуки" };
const HP_STATUS = { Healthy: "неушкоджений", Injured: "поранений", Bloodied: "тяжко поранений", Critical: "при смерті", Dead: "загинув" };
const KIND_LABELS = { adventure: "пригоди", monster: "монстри", encounter: "зустрічі" };
const LANGUAGES = ["English", "Spanish", "German", "French", "Portuguese", "Polish"];
const SYSTEM_PHRASES = { "Dungeons & Dragons": "за D&D", Pathfinder: "за Pathfinder", "Old-School Essentials": "за Old-School Essentials",
  "Shadowdark RPG": "за Shadowdark", "Dungeon Crawl Classics": "за Dungeon Crawl Classics", "System-agnostic": "без прив'язки до системи" };
const REF = { settings: {}, systems: [] };

// ---------------------------------------------------------------- мережа
async function api(url, options) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw Object.assign(new Error("сервер не відповідає"), { status: 0 });
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const reasons = body.reasons ? body.reasons.join("; ") : body.detail || response.statusText;
    throw Object.assign(new Error(`${response.status}: ${reasons}`), { status: response.status });
  }
  return body;
}

async function loadReference() {
  try {
    const [settings, systems] = await Promise.all([api("/static/data/settings.json"), api("/static/data/systems.json")]);
    settings.forEach((s) => (REF.settings[s.setting_id] = s));
    REF.systems = systems;
  } catch (error) {
    console.warn("списки вручну недоступні", error);
  }
}

// ---------------------------------------------------------------- спільні фрагменти
const settingName = (id) => (id && REF.settings[id] ? REF.settings[id].name : null);
const range = (a, b) => (a == null && b == null ? null : a === b || b == null ? `${a}` : `${a ?? "?"}–${b}`);

// Фрагмент із підсвіченими збігами. Очищення (переноси рядків, розмітка Discord
// у репліках ведучих) діє на кожен шматок окремо, тож позиції збігів не зсуваються
const tidy = (text) => text.replace(/```|\*\*|__|`/g, "").replace(/\s+/g, " ");
function snippetHTML(snippet) {
  let out = "";
  let position = 0;
  for (const [start, end] of snippet.highlights || []) {
    out += esc(tidy(snippet.text.slice(position, start))) + "<mark>" + esc(snippet.text.slice(start, end)) + "</mark>";
    position = end;
  }
  return (out + esc(tidy(snippet.text.slice(position)))).trim();
}

function hashHue(text) {
  let h = 0;
  for (const c of String(text)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h % 360;
}

// Обкладинка з itch.io або, якщо її немає чи вона не завантажилась, «печатка» з першою літерою назви
const SIGIL_HUES = [4, 16, 28, 40, 150, 188, 214, 268, 320];
const ARTICLES = new Set(["the", "a", "an", "of", "la", "le", "les", "el", "der", "die", "das"]);
function sigil(title) {
  const hue = SIGIL_HUES[hashHue(title) % SIGIL_HUES.length];
  const words = String(title).split(/\s+/).map((w) => w.replace(/^[^\p{L}\p{N}]+/u, "")).filter(Boolean);
  const word = words.find((w) => !ARTICLES.has(w.toLowerCase())) || words[0] || "?";
  return `<div class="sigil" style="--h:${hue}"><span>${esc(word[0].toUpperCase())}</span></div>`;
}
function coverHTML(url, title, badges = "") {
  const img = url ? `<img src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-title="${esc(title)}">` : "";
  return `<div class="cover">${img || sigil(title)}${badges ? `<div class="badges">${badges}</div>` : ""}</div>`;
}
document.addEventListener("error", (event) => {
  const img = event.target;
  if (img.tagName === "IMG" && img.closest(".cover") && !img.dataset.failed) {
    img.dataset.failed = "1";
    img.insertAdjacentHTML("afterend", sigil(img.dataset.title || ""));
    img.remove();
  }
}, true);

function starsHTML(avg, count) {
  if (avg == null) return "";
  const lit = Math.round(avg);
  const stars = Array.from({ length: 5 }, (_, i) => `<svg class="${i < lit ? "lit" : ""}" aria-hidden="true"><use href="#i-star"/></svg>`).join("");
  return `<span class="rating" title="середня оцінка на itch.io"><span class="stars">${stars}</span>${NF1.format(avg)}${count != null ? `<span class="count">(${fmt(count)})</span>` : ""}</span>`;
}

function priceBadge(a) {
  if (a.is_free) return '<span class="badge free">безкоштовно</span>';
  if (a.price != null) return `<span class="badge price">${USD.format(a.price)}</span>`;
  return "";
}

function traitsHTML(a) {
  const items = [
    range(a.level_min, a.level_max) && `рівні ${range(a.level_min, a.level_max)}`,
    range(a.players_min, a.players_max) && `${range(a.players_min, a.players_max)} ${plural(a.players_max ?? a.players_min, "гравець", "гравці", "гравців")}`,
    a.duration_hours != null && `≈ ${fmt1(a.duration_hours)} год`,
    a.system && (a.system === "Dungeons & Dragons" && a.edition ? `D&D ${a.edition}` : a.system),
    settingName(a.setting_id),
  ].filter(Boolean);
  return items.length ? `<ul class="traits">${items.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : "";
}

function tomeHTML(a) {
  const badges = priceBadge(a) + (a.origin === "generated" ? '<span class="badge gen">згенерована</span>' : "");
  const snippet = (a.snippets || [])[0];
  return `<a class="tome" href="#/adventure/${enc(a.adventure_id)}">
    ${coverHTML(a.cover_image_url, a.title, badges)}
    <div class="tome-body">
      <h3>${esc(a.title)}</h3>
      ${starsHTML(a.rating_avg, a.ratings_count)}
      ${traitsHTML(a)}
      ${snippet ? `<div class="snippet">${snippetHTML(snippet)}</div>` : a.synopsis ? `<div class="snippet">${esc(a.synopsis)}</div>` : ""}
    </div></a>`;
}

const emptyState = (title, text) => `<div class="slab empty-state"><h3>${esc(title)}</h3><p class="muted" style="margin:0 auto">${text}</p></div>`;
const errorState = (error) => (error.status === 404
  ? emptyState("Такого запису немає", 'Можливо, посилання застаріло або в адресі помилка. Поверніться назад чи почніть <a href="#/">з каталогу пригод</a>.')
  : emptyState("Не вдалося отримати дані", `Причина: ${esc(error.message)}. Спробуйте ще раз за хвилину; якщо помилка повторюється, перевірте, чи запущено процес api.`));
const loading = '<div class="muted small">Завантаження…</div>';
const backButton = '<button class="back" type="button" data-back><svg aria-hidden="true"><use href="#i-back"/></svg>Назад</button>';

// ---------------------------------------------------------------- маршрутизація
// Адреси розділів — у частині після «#», тож працюють кнопки «назад» і закладки:
// #/?q=…&level=… — пошук пригод, #/adventure/<id>, #/bestiary?…, #/monster/<id>,
// #/chronicles?…, #/character/<id>, #/author/<id>, #/halls, #/atlas?…, #/forge
const SECTION_OF = { "": "adventures", adventure: "adventures", author: "adventures", bestiary: "bestiary", monster: "bestiary",
  chronicles: "chronicles", character: "chronicles", halls: "halls", atlas: "atlas", forge: "forge" };
const VIEWS = {};
let current = { section: null, path: null };
let lastPointer = null;

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  return { name: parts[0] || "", id: parts[1] ? decodeURIComponent(parts[1]) : null, params: new URLSearchParams(query), path };
}

function setNav(section) {
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("on", a.dataset.nav === section));
}

async function route() {
  const target = parseHash();
  const section = SECTION_OF[target.name] || "adventures";
  const view = VIEWS[target.name] || VIEWS[""];
  const big = current.section !== null && current.section !== section;
  const samePage = current.path === target.path && current.section === section;
  current = { section, path: target.path };
  setNav(section);
  const main = $("view");
  const origin = lastPointer;
  lastPointer = null;
  const render = () => {
    main.innerHTML = view.html(target);
    if (!samePage) window.scrollTo(0, 0);
    document.title = `${view.title(target)} — D&D Adventures`;
    view.after?.(main, target);
  };
  if (current.section === null || samePage) render();
  else await FX.transition(main, render, origin, big);
}

document.addEventListener("click", (event) => {
  const nav = event.target.closest(".nav a, .brand");
  if (nav) lastPointer = { x: event.clientX, y: event.clientY };
  const back = event.target.closest("[data-back]");
  if (back) {
    event.preventDefault();
    if (history.length > 1) history.back();
    else location.hash = "#/";
  }
});
window.addEventListener("hashchange", route);

function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

// ================================================================ ПРИГОДИ
const ADVENTURE_FILTERS = ["q", "players", "level", "max_duration_hours", "system", "is_free", "setting_id", "difficulty",
  "publication_language", "year_from", "year_to", "tags"];
const SHELVES = [
  { title: "Для першої гри", note: "пригоди для персонажів 1-го рівня з найвищими оцінками", params: { level: "1", sort: "rating" } },
  { title: "Безкоштовні й найкраще оцінені", note: "можна завантажити й провести вже сьогодні", params: { is_free: "true", sort: "rating" } },
  { title: "Пригода на один вечір", note: "до чотирьох годин гри", params: { max_duration_hours: "4", sort: "rating" } },
  { title: "Моторошні історії", note: "модулі з тегом horror", params: { tags: "horror", sort: "rating" } },
];

function partyForm(params) {
  const sel = (name, options) => `<select name="${name}" aria-label="${name}">${options.map(([value, label]) =>
    `<option value="${esc(value)}"${(params.get(name) || "") === value ? " selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
  const players = [["", "будь-якої партії"], ["1", "одного гравця"], ...Array.from({ length: 7 }, (_, i) => [`${i + 2}`, `${i + 2} гравців`])];
  const levels = [["", "будь-якого рівня"], ...Array.from({ length: 20 }, (_, i) => [`${i + 1}`, `${i + 1}-го рівня`])];
  const durations = [["", "без обмеження часу"], ["2", "на коротку сесію"], ["4", "на один вечір"], ["8", "на довгу сесію"], ["24", "на кілька сесій"]];
  const systems = [["", "за будь-якою системою"], ...[...new Set(REF.systems.map((s) => s.system))].map((n) => [n, SYSTEM_PHRASES[n] || `за ${n}`])];
  const settings = Object.values(REF.settings);
  const opt = (name, values, empty) => `<select class="field" name="${name}"><option value="">${empty}</option>${values.map(([v, l]) =>
    `<option value="${esc(v)}"${params.get(name) === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const advanced = ["is_free", "setting_id", "difficulty", "publication_language", "year_from", "year_to", "tags", "sort"].some((k) => params.get(k));
  return `<form class="slab party" id="party" autocomplete="off">
    <p class="sentence">Шукаємо пригоду для ${sel("players", players)} ${sel("level", levels)} ${sel("max_duration_hours", durations)} ${sel("system", systems)}.</p>
    <div class="quest">
      <input class="field" name="q" value="${esc(params.get("q") || "")}" placeholder="Про що вона? Англійською: cursed village, dragon lair, heist" aria-label="Текст запиту англійською">
      <button class="rune" type="submit"><svg aria-hidden="true"><use href="#i-search"/></svg>Знайти пригоду</button>
    </div>
    <details class="more"${advanced ? " open" : ""}><summary>Більше умов</summary>
      <div class="more-grid">
        <label class="lab">Ігровий світ${opt("setting_id", settings.map((s) => [s.setting_id, s.name]), "будь-який")}</label>
        <label class="lab">Складність${opt("difficulty", ["1", "2", "3", "4", "5"].map((d) => [d, `${d} з 5`]), "будь-яка")}</label>
        <label class="lab">Мова видання${opt("publication_language", LANGUAGES.map((l) => [l, l]), "будь-яка")}</label>
        <label class="lab">Рік від<input class="field" type="number" name="year_from" min="1974" max="2100" value="${esc(params.get("year_from") || "")}"></label>
        <label class="lab">Рік до<input class="field" type="number" name="year_to" min="1974" max="2100" value="${esc(params.get("year_to") || "")}"></label>
        <label class="lab">Теги через кому<input class="field" name="tags" value="${esc(params.getAll("tags").join(", "))}" placeholder="horror, one-shot"></label>
        <label class="lab">Порядок${opt("sort", [["rating", "за оцінкою"], ["reviews", "за кількістю відгуків"], ["year", "за роком"], ["duration", "за тривалістю"]], "за релевантністю")}</label>
        <label class="check" style="align-self:end;padding-bottom:10px"><input type="checkbox" name="is_free" value="true"${params.get("is_free") === "true" ? " checked" : ""}>лише безкоштовні</label>
      </div>
    </details>
    <p class="example">Наприклад: ${["murder mystery", "goblin cave", "pirate"].map((q) => `<button type="button" data-example="${q}">${q}</button>`).join(", ")}</p>
  </form>`;
}

function formParams(form) {
  const params = new URLSearchParams();
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") {
      if (el.checked) params.set(el.name, el.value);
      continue;
    }
    const value = (el.value || "").trim();
    if (!value) continue;
    if (el.name === "tags") value.split(",").map((t) => t.trim()).filter(Boolean).forEach((t) => params.append("tags", t));
    else params.set(el.name, value);
  }
  return params;
}

const hasAdventureQuery = (params) => ADVENTURE_FILTERS.some((k) => params.getAll(k).some((v) => v.trim()));

VIEWS[""] = {
  title: (t) => (hasAdventureQuery(t.params) ? "Пошук пригод" : "Пригоди"),
  html: (t) => `
    <section class="hero">
      <h1 class="gilded">Знайди пригоду для своєї партії</h1>
      <p class="lead">Понад дві тисячі модулів з itch.io і згенеровані пригоди з монстрами, яких найчастіше зустрічають у реальних іграх.</p>
      ${partyForm(t.params)}
    </section>
    <div id="adv-results"></div>`,
  after(main, t) {
    const form = $("party");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      FX.strike(form.querySelector(".rune"));
      go(`#/?${formParams(form)}`);
    });
    form.querySelectorAll(".sentence select").forEach((s) => s.addEventListener("change", () => go(`#/?${formParams(form)}`)));
    form.addEventListener("click", (e) => {
      const example = e.target.closest("[data-example]");
      if (!example) return;
      form.elements.q.value = example.dataset.example;
      form.requestSubmit();
    });
    if (hasAdventureQuery(t.params)) runAdventureSearch(t.params);
    else renderShelves();
  },
};

// Добірки однакові для всіх і змінюються повільно, а запит без тексту по всьому
// каталогу важкий (підрахунок усіх збігів і теги для фасетів). Тому результат
// тримаємо 5 хвилин у пам'яті сторінки, а полицю запитуємо, лише коли вона
// наближається до видимої області
const SHELF_TTL_MS = 5 * 60 * 1000;
const shelfMemo = new Map();
let shelfObserver = null;

function shelfFresh(params) {
  const hit = shelfMemo.get(String(new URLSearchParams(params)));
  return hit && Date.now() - hit.at < SHELF_TTL_MS ? hit.items : null;
}

async function fillShelf(i) {
  const shelf = () => $(`shelf-${i}`);
  const params = SHELVES[i].params;
  try {
    let items = shelfFresh(params);
    if (!items) {
      const key = String(new URLSearchParams(params));
      items = (await api(`/api/search/adventures?${key}`)).items;
      shelfMemo.set(key, { at: Date.now(), items });
    }
    if (shelf()) shelf().innerHTML = items.slice(0, 12).map(tomeHTML).join("") || '<div class="muted">Поки порожньо: каталог ще наповнюється.</div>';
  } catch (error) {
    if (shelf()) shelf().innerHTML = `<div class="error small">${esc(error.message)}</div>`;
  }
}

function renderShelves() {
  const box = $("adv-results");
  box.innerHTML = SHELVES.map((s, i) => `<section class="section">
      <div class="section-head"><div><h2>${esc(s.title)}</h2><div class="muted small">${esc(s.note)}</div></div>
        <a class="ghost" href="#/?${new URLSearchParams(s.params)}">Дивитися всі</a></div>
      <div class="shelf" id="shelf-${i}" data-shelf="${i}">${loading}</div></section>`).join("");
  shelfObserver?.disconnect();
  const waiting = SHELVES.map((s, i) => i).filter((i) => {
    if (!shelfFresh(SHELVES[i].params)) return true;
    fillShelf(i);
    return false;
  });
  if (!("IntersectionObserver" in window)) {
    waiting.forEach(fillShelf);
    return;
  }
  shelfObserver = new IntersectionObserver((entries, observer) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer.unobserve(entry.target);
      fillShelf(Number(entry.target.dataset.shelf));
    }
  }, { rootMargin: "300px 0px" });
  waiting.forEach((i) => shelfObserver.observe($(`shelf-${i}`)));
}

async function runAdventureSearch(params) {
  const box = $("adv-results");
  const page = Number(params.get("page") || 1);
  box.innerHTML = `<div class="results-bar"><h2>Шукаємо…</h2></div>`;
  try {
    const result = await api(`/api/search/adventures?${params}`);
    const pages = Math.max(1, Math.ceil(result.total / result.page_size));
    const activeTags = new Set(params.getAll("tags"));
    const tagChips = Object.entries(result.tag_counts || {}).slice(0, 24)
      .map(([tag, count]) => `<button class="chip${activeTags.has(tag) ? " on" : ""}" type="button" data-tag="${esc(tag)}">${esc(tag)}<b>${fmt(count)}</b></button>`).join("");
    const sortOptions = [["", "за релевантністю"], ["rating", "за оцінкою"], ["reviews", "за відгуками"], ["year", "за роком"], ["duration", "за тривалістю"]];
    const word = plural(result.total, "пригоду", "пригоди", "пригод");
    box.innerHTML = `
      <div class="results-bar">
        <h2>${result.total ? `Знайдено ${fmt(result.total)} ${word}` : "Нічого не знайдено"}</h2>
        <label class="sorter">Порядок<select class="field" id="adv-sort">${sortOptions.map(([v, l]) =>
          `<option value="${v}"${(params.get("sort") || "") === v ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      </div>
      <div class="inscription"><span>час відповіді <b>${fmt(result.latency_ms)} мс</b></span><span>з кешу: <b>${result.cache_hit ? "так" : "ні"}</b></span>${result.normalized_query ? `<span>нормальна форма запиту: <b>${esc(result.normalized_query)}</b></span>` : ""}</div>
      ${tagChips ? `<div class="chips facets" id="facets">${tagChips}</div>` : ""}
      ${result.items.length ? `<div class="grid">${result.items.map(tomeHTML).join("")}</div>`
        : emptyState("Таких пригод поки немає", "Приберіть частину умов у реченні вгорі або спробуйте інші слова англійською: каталог і довідник англомовні.")}
      ${result.total > result.page_size ? `<div class="pager">
        <button class="ghost" data-page="${page - 1}"${page <= 1 ? " disabled" : ""}>Попередня</button>
        <span>сторінка ${page} з ${fmt(pages)}</span>
        <button class="ghost" data-page="${page + 1}"${page >= pages ? " disabled" : ""}>Наступна</button></div>` : ""}`;
    $("adv-sort").addEventListener("change", (e) => {
      const next = new URLSearchParams(params);
      e.target.value ? next.set("sort", e.target.value) : next.delete("sort");
      next.delete("page");
      go(`#/?${next}`);
    });
    box.querySelector("#facets")?.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-tag]");
      if (!chip) return;
      const tags = new Set(params.getAll("tags"));
      tags.has(chip.dataset.tag) ? tags.delete(chip.dataset.tag) : tags.add(chip.dataset.tag);
      const next = new URLSearchParams(params);
      next.delete("tags");
      next.delete("page");
      tags.forEach((tag) => next.append("tags", tag));
      go(`#/?${next}`);
    });
    box.querySelector(".pager")?.addEventListener("click", (e) => {
      const button = e.target.closest("[data-page]");
      if (!button || button.disabled) return;
      const next = new URLSearchParams(params);
      next.set("page", button.dataset.page);
      go(`#/?${next}`);
    });
  } catch (error) {
    box.innerHTML = errorState(error);
  }
}

// ---------------------------------------------------------------- сторінка пригоди
VIEWS.adventure = {
  title: () => "Пригода",
  html: () => `${backButton}<div id="adv">${loading}</div>`,
  async after(main, t) {
    const box = $("adv");
    let a;
    try {
      a = await api(`/api/adventures/${enc(t.id)}`);
    } catch (error) {
      box.innerHTML = errorState(error);
      return;
    }
    document.title = `${a.title} — D&D Adventures`;
    const fact = (label, value) => (value == null || value === "" ? "" : `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`);
    const price = a.is_free ? "безкоштовно" : a.price != null ? USD.format(a.price) : null;
    const origin = { generated: "згенерована системою", csv: "каталог itch.io", crawler: "каталог itch.io, зібрано краулером" }[a.origin] || a.origin;
    box.innerHTML = `
      ${a.cover_image_url ? `<div class="page-backdrop" style="--cover:url('${esc(a.cover_image_url)}')"></div>` : ""}
      <div class="adv-head">
        <div class="frame">${coverHTML(a.cover_image_url, a.title, priceBadge(a))}</div>
        <div class="adv-title">
          <h1 class="gilded">${esc(a.title)}</h1>
          ${a.hook ? `<p class="hook">${esc(a.hook)}</p>` : ""}
          ${a.rating_avg != null ? starsHTML(a.rating_avg, a.ratings_count) : ""}
          <dl class="ledger">
            ${fact("Рівні персонажів", range(a.level_min, a.level_max))}${fact("Гравців", range(a.players_min, a.players_max))}
            ${fact("Тривалість", a.duration_hours != null ? `≈ ${fmt1(a.duration_hours)} год` : null)}${fact("Сесій", a.session_count)}
            ${fact("Складність", a.difficulty != null ? `${a.difficulty} з 5` : null)}${fact("Система", [a.system, a.edition].filter(Boolean).join(" "))}
            ${fact("Ігровий світ", settingName(a.setting_id))}${fact("Рік", a.published_year)}${fact("Сторінок", a.page_count)}
            ${fact("Видавництво", a.publisher)}${fact("Мова видання", a.publication_language)}${fact("Жанр", a.genre && a.genre.replace(/\s+,/g, ","))}
            ${fact("Статус", a.status)}${fact("Ціна", price)}${fact("Відгуків", a.reviews_count || null)}${fact("Джерело", origin)}
          </dl>
          <div class="actions">
            ${a.source_url ? `<a class="rune" href="${esc(a.source_url)}" target="_blank" rel="noopener">Відкрити на itch.io<svg aria-hidden="true"><use href="#i-out"/></svg></a>` : ""}
            ${a.similar_urls.length ? `<a class="ghost" href="${esc(a.similar_urls[0])}" target="_blank" rel="noopener">Схожі на itch.io<svg aria-hidden="true"><use href="#i-out"/></svg></a>` : ""}
          </div>
        </div>
      </div>
      ${a.synopsis ? `<section class="section prose"><h2>Про пригоду</h2><p>${esc(a.synopsis)}</p></section>` : ""}
      ${a.tags.length ? `<section class="section"><h2>Теги</h2><div class="chips">${a.tags.map((tag) => `<a class="chip" href="#/?tags=${enc(tag)}">${esc(tag)}</a>`).join("")}</div></section>` : ""}
      ${a.monster_ids.length ? `<section class="section"><h2>Монстри</h2><div class="people" id="adv-monsters">${a.monster_ids.map((id) =>
        `<a class="person" href="#/monster/${enc(id)}" data-monster="${esc(id)}"><b>…</b><span></span></a>`).join("")}</div></section>` : ""}
      ${a.author_ids.length ? `<section class="section"><h2>${a.author_ids.length > 1 ? "Автори" : "Автор"}</h2><div class="people" id="adv-authors">${a.author_ids.map((id) =>
        `<a class="person" href="#/author/${enc(id)}" data-author="${esc(id)}"><b>…</b><span>профіль і роботи</span></a>`).join("")}</div></section>` : ""}
      <section class="section" id="adv-chain-box" hidden><h2>Ланцюжок кампанії</h2><p class="muted small">Пригоди того самого світу, де рівні йдуть поспіль, а монстри не повторюються.</p><ol class="chain" id="adv-chain"></ol></section>
      <section class="section"><h2>Схожі пригоди</h2><p class="muted small">Мають спільні теги або монстрів із цією пригодою.</p><div class="reco two" id="adv-similar">${loading}</div></section>
      ${a.reviews.length ? `<section class="section"><h2>Відгуки гравців</h2><div class="quotes">${a.reviews.map((r, i) => `<blockquote class="quote"${i >= 4 ? " hidden" : ""}><p>${esc(r.text)}</p>
        <footer>${esc(r.author_name)}${r.posted_at ? `, ${esc(new Date(r.posted_at).toLocaleDateString("uk-UA"))}` : ""}</footer></blockquote>`).join("")}</div>
        ${a.reviews.length > 4 ? `<button class="ghost more-quotes" type="button">Показати ще ${a.reviews.length - 4}</button>` : ""}</section>` : ""}
      ${a.location_names.length ? `<section class="section"><h2>Локації</h2><div class="chips">${a.location_names.map((n) => `<span class="chip">${esc(n)}</span>`).join("")}</div></section>` : ""}`;
    box.querySelector(".more-quotes")?.addEventListener("click", (event) => {
      box.querySelectorAll(".quote[hidden]").forEach((q) => (q.hidden = false));
      event.currentTarget.remove();
    });
    a.author_ids.forEach(async (id) => {
      const el = box.querySelector(`[data-author="${CSS.escape(id)}"]`);
      try {
        const author = await api(`/api/authors/${enc(id)}`);
        el.querySelector("b").textContent = author.name;
        el.querySelector("span").textContent = `${fmt(author.adventures_count)} ${plural(author.adventures_count, "пригода", "пригоди", "пригод")} у каталозі`;
      } catch { el.querySelector("b").textContent = id; }
    });
    a.monster_ids.forEach(async (id) => {
      const el = box.querySelector(`[data-monster="${CSS.escape(id)}"]`);
      try {
        const m = await api(`/api/monsters/${enc(id)}`);
        el.querySelector("b").textContent = m.name;
        el.querySelector("span").textContent = `${CREATURE_TYPES[m.type] || m.type}, рівень небезпеки ${fmt1(m.challenge_rating)}`;
      } catch { el.querySelector("b").textContent = id; }
    });
    api(`/api/adventures/${enc(a.adventure_id)}/similar`).then((items) => {
      const shared = (r) => [
        r.shared_monsters.length && `${r.shared_monsters.length} ${plural(r.shared_monsters.length, "спільний монстр", "спільні монстри", "спільних монстрів")}`,
        r.shared_tags.length && `${r.shared_tags.length} ${plural(r.shared_tags.length, "спільний тег", "спільні теги", "спільних тегів")}`,
        range(r.level_min, r.level_max) && `рівні ${range(r.level_min, r.level_max)}`,
      ].filter(Boolean).join(", ");
      $("adv-similar").innerHTML = items.length ? items.map((r) => `<a href="#/adventure/${enc(r.adventure_id)}" title="${esc(r.reason || "")}">
        <b>${esc(r.title)}</b><span>${esc(shared(r))}</span></a>`).join("")
        : '<div class="muted">Схожих пригод поки немає.</div>';
    }).catch((error) => ($("adv-similar").innerHTML = `<div class="error small">${esc(error.message)}</div>`));
    api(`/api/adventures/${enc(a.adventure_id)}/chain?level_from=1&level_to=20`).then((items) => {
      if (items.length < 2) return;
      $("adv-chain-box").hidden = false;
      $("adv-chain").innerHTML = items.map((r) => `<li class="${r.adventure_id === a.adventure_id ? "here" : ""}">
        <a href="#/adventure/${enc(r.adventure_id)}">${esc(r.title)}</a><span class="muted small">рівні ${esc(range(r.level_min, r.level_max))}</span></li>`).join("");
    }).catch(() => {});
  },
};

// ---------------------------------------------------------------- сторінка автора
VIEWS.author = {
  title: () => "Автор",
  html: () => `${backButton}<div id="author">${loading}</div>`,
  async after(main, t) {
    const box = $("author");
    try {
      const a = await api(`/api/authors/${enc(t.id)}`);
      document.title = `${a.name} — D&D Adventures`;
      box.innerHTML = `
        <div class="profile-head">
          <div><h1 class="gilded">${esc(a.name)}</h1>
            ${a.profile_url ? `<p style="margin:8px 0 0"><a href="${esc(a.profile_url)}" target="_blank" rel="noopener">${esc(a.profile_url.replace(/^https?:\/\//, ""))}</a></p>` : ""}</div>
        </div>
        <dl class="ledger">
          <div><dt>Пригод у каталозі</dt><dd>${fmt(a.adventures_count)}</dd></div>
          <div><dt>Середня оцінка</dt><dd>${a.avg_rating != null ? NF1.format(a.avg_rating) : "—"}</dd></div>
          <div><dt>Перша публікація</dt><dd>${a.first_published_year ?? "—"}</dd></div>
          <div><dt>Платформа</dt><dd>${esc(a.platform || "—")}</dd></div>
        </dl>
        ${a.bio ? `<section class="section prose"><h2>Про автора</h2><p>${esc(a.bio)}</p></section>` : ""}
        <section class="section"><div class="section-head"><h2>Роботи</h2>${a.adventures.length < a.adventures_count
          ? `<span class="muted small">${fmt(a.adventures.length)} найновіших із ${fmt(a.adventures_count)}</span>` : ""}</div>
          ${a.adventures.length ? `<div class="grid">${a.adventures.map(tomeHTML).join("")}</div>` : '<div class="muted">Робіт у каталозі немає.</div>'}</section>
        ${a.co_authors.length ? `<section class="section"><h2>Співавтори</h2><div class="chips">${a.co_authors.map(([id, name]) => `<a class="chip" href="#/author/${enc(id)}">${esc(name)}</a>`).join("")}</div></section>` : ""}`;
    } catch (error) {
      box.innerHTML = errorState(error);
    }
  },
};

// ================================================================ БЕСТІАРІЙ
const MONSTER_FILTERS = ["q", "party_level", "challenge_min", "challenge_max", "creature_type", "environment"];

function beastHTML(m, extra = "") {
  const env = (m.environment || []).map((e) => ENVIRONMENTS[e] || e).join(", ");
  const kind = [CREATURE_TYPES[m.type] || m.type, env].filter(Boolean).join(", ");
  return `<a class="beast" href="#/monster/${enc(m.monster_id)}">
    ${m.challenge_rating != null ? `<span class="cr" title="рівень небезпеки">${esc(crText(m.challenge_rating))}</span>` : '<span class="cr wait" aria-hidden="true"></span>'}
    <span class="beast-text"><span class="beast-name">${esc(m.name)}</span>${kind ? `<span class="beast-kind">${esc(kind)}</span>` : ""}
    ${m.play_count ? `<span class="beast-seen">у реальних боях: ${fmt(m.play_count)}</span>` : ""}${extra}</span></a>`;
}
function crText(cr) {
  return { 0.125: "⅛", 0.25: "¼", 0.5: "½" }[cr] || NF1.format(cr);
}

VIEWS.bestiary = {
  title: () => "Бестіарій",
  html: (t) => {
    const p = t.params;
    const options = (map, name, empty) => `<select class="field" name="${name}"><option value="">${empty}</option>${Object.entries(map).map(([v, l]) =>
      `<option value="${v}"${p.get(name) === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    return `
      <div class="forge-head"><div><h1 class="gilded">Бестіарій</h1>
        <p class="lead">334 істоти довідника 5e SRD. Шукайте за описом здібності, рівнем небезпеки, середовищем або підбирайте суперників під рівень партії.</p></div></div>
      <form class="slab filters" id="beast-form" autocomplete="off">
        <label class="lab wide">Здібність або назва англійською<input class="field" name="q" value="${esc(p.get("q") || "")}" placeholder="breath weapon, paralyzing touch, poison"></label>
        <label class="lab short">Рівень партії<input class="field" type="number" name="party_level" min="1" max="20" value="${esc(p.get("party_level") || "")}"></label>
        <label class="lab short">Небезпека від<input class="field" type="number" name="challenge_min" min="0" step="0.125" value="${esc(p.get("challenge_min") || "")}"></label>
        <label class="lab short">Небезпека до<input class="field" type="number" name="challenge_max" min="0" step="0.125" value="${esc(p.get("challenge_max") || "")}"></label>
        <label class="lab">Тип істоти${options(CREATURE_TYPES, "creature_type", "будь-який")}</label>
        <label class="lab">Середовище${options(ENVIRONMENTS, "environment", "будь-яке")}</label>
        <div class="go"><button class="rune" type="submit"><svg aria-hidden="true"><use href="#i-search"/></svg>Знайти</button>
          <a class="ghost" href="#/bestiary">Скинути</a></div>
      </form>
      <div id="beast-results" class="section"></div>`;
  },
  after(main, t) {
    const form = $("beast-form");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      FX.strike(form.querySelector(".rune"));
      go(`#/bestiary?${formParams(form)}`);
    });
    const params = t.params;
    if (MONSTER_FILTERS.some((k) => params.get(k))) runMonsterSearch(params);
    else renderPopularMonsters();
  },
};

async function renderPopularMonsters() {
  const box = $("beast-results");
  box.innerHTML = `<div class="section-head"><div><h2>Найчастіші в реальних боях</h2>
    <div class="muted small">Скільки разів монстр трапився в записах ігор FIREBALL</div></div><a class="ghost" href="#/halls">Уся зала слави</a></div>
    <div class="beasts" id="beast-grid">${loading}</div>`;
  try {
    const stats = await api("/api/statistics/monsters?limit=24");
    const grid = $("beast-grid");
    grid.innerHTML = stats.popular_monsters.map(([id, name, count]) =>
      beastHTML({ monster_id: id, name, play_count: count, challenge_rating: null })).join("") || '<div class="muted">Статистики ще немає: завантажте CSV.</div>';
    // тип, рівень небезпеки й середовище — з карток монстрів
    stats.popular_monsters.forEach(async ([id], i) => {
      try {
        const m = await api(`/api/monsters/${enc(id)}`);
        const tile = grid.children[i];
        if (tile) tile.outerHTML = beastHTML(m);
      } catch { /* плитка лишається з назвою */ }
    });
  } catch (error) {
    box.innerHTML = errorState(error);
  }
}

async function runMonsterSearch(params) {
  const box = $("beast-results");
  box.innerHTML = loading;
  try {
    const result = await api(`/api/search/monsters?${params}`);
    const page = Number(params.get("page") || 1);
    const pages = Math.max(1, Math.ceil(result.total / result.page_size));
    box.innerHTML = `<div class="results-bar"><h2>${result.total ? `Знайдено ${fmt(result.total)} ${plural(result.total, "істоту", "істоти", "істот")}` : "Нічого не знайдено"}</h2></div>
      <div class="inscription"><span>час відповіді <b>${fmt(result.latency_ms)} мс</b></span><span>з кешу: <b>${result.cache_hit ? "так" : "ні"}</b></span></div>
      ${result.items.length ? `<div class="beasts">${result.items.map((m) => beastHTML(m, (m.snippets || [])[0] ? `<span class="snippet small">${snippetHTML(m.snippets[0])}</span>` : "")).join("")}</div>`
        : emptyState("Такої істоти не знайшлося", "Спробуйте інше слово англійською або розширте діапазон рівня небезпеки.")}
      ${result.total > result.page_size ? `<div class="pager"><button class="ghost" data-page="${page - 1}"${page <= 1 ? " disabled" : ""}>Попередня</button>
        <span>сторінка ${page} з ${fmt(pages)}</span><button class="ghost" data-page="${page + 1}"${page >= pages ? " disabled" : ""}>Наступна</button></div>` : ""}`;
    box.querySelector(".pager")?.addEventListener("click", (e) => {
      const button = e.target.closest("[data-page]");
      if (!button || button.disabled) return;
      const next = new URLSearchParams(params);
      next.set("page", button.dataset.page);
      go(`#/bestiary?${next}`);
    });
  } catch (error) {
    box.innerHTML = errorState(error);
  }
}

// ---------------------------------------------------------------- сторінка монстра
const modifier = (score) => {
  const m = Math.floor((score - 10) / 2);
  return `${m >= 0 ? "+" : "−"}${Math.abs(m)}`;
};

VIEWS.monster = {
  title: () => "Монстр",
  html: () => `${backButton}<div id="monster">${loading}</div>`,
  async after(main, t) {
    const box = $("monster");
    try {
      const m = await api(`/api/monsters/${enc(t.id)}`);
      document.title = `${m.name} — D&D Adventures`;
      const b = m.statblock || {};
      const list = (items) => (items || []).join(", ");
      const dict = (d) => Object.entries(d || {}).map(([k, v]) => `${k} ${typeof v === "number" ? (v >= 0 ? "+" : "") + v : v}`).join(", ");
      const speed = Object.entries(b.speed || {}).map(([k, v]) => (k === "walk" ? `${v} фт.` : `${k} ${v} фт.`)).join(", ");
      const line = (label, value) => (value ? `<p class="line"><b>${esc(label)}</b> ${esc(value)}</p>` : "");
      const abilities = ["str", "dex", "con", "int", "wis", "cha"].map((k) => {
        const v = (b.abilities_scores || {})[k];
        return `<div><b>${k.toUpperCase()}</b>${v != null ? `${v} (${modifier(v)})` : "—"}</div>`;
      }).join("");
      const acts = (title, items) => (items && items.length ? `<h3>${title}</h3>${items.map((x) => `<p class="act"><b>${esc(x.name)}.</b> ${esc(x.description)}</p>`).join("")}` : "");
      box.innerHTML = `
        <div class="monster-layout">
          <article class="statblock">
            <h1>${esc(m.name)}</h1>
            <p class="kind">${esc([m.size, m.type, m.subtype && `(${m.subtype})`].filter(Boolean).join(" "))}${m.alignment ? `, ${esc(m.alignment)}` : ""}</p>
            <hr class="taper">
            ${line("Клас броні", b.armor_class != null ? `${b.armor_class}${b.ac_type ? ` (${b.ac_type})` : ""}` : null)}
            ${line("Хіти", b.hit_points != null ? `${b.hit_points}${b.hit_dice ? ` (${b.hit_dice})` : ""}` : null)}
            ${line("Швидкість", speed)}
            <hr class="taper"><div class="abil">${abilities}</div><hr class="taper">
            ${line("Рятівні кидки", dict(b.saving_throws))}${line("Навички", dict(b.skills))}
            ${line("Вразливість", list(b.damage_vulnerabilities))}${line("Стійкість", list(b.damage_resistances))}
            ${line("Імунітет до шкоди", list(b.damage_immunities))}${line("Імунітет до станів", list(b.condition_immunities))}
            ${line("Чуття", Object.entries(b.senses || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", "))}
            ${line("Мови", list(b.languages) || "—")}
            ${line("Небезпека", `${crText(m.challenge_rating)}${m.xp != null ? ` (${fmt(m.xp)} досвіду)` : ""}`)}
            <hr class="taper">
            ${acts("Особливості", b.special_abilities)}${acts("Дії", b.actions)}${acts("Легендарні дії", b.legendary_actions)}${acts("Реакції", b.reactions)}
          </article>
          <div>
            <h2>У реальних боях</h2>
            <div class="carved"><b>${fmt(m.play_count)}</b><span>${plural(m.play_count, "поява", "появи", "появ")} у записах ігор FIREBALL</span></div>
            ${m.environment.length ? `<section class="section"><h2>Середовище</h2><div class="chips">${m.environment.map((e) =>
              `<a class="chip" href="#/bestiary?environment=${enc(e)}">${esc(ENVIRONMENTS[e] || e)}</a>`).join("")}</div></section>` : ""}
            ${m.lore ? `<section class="section prose"><h2>Опис</h2><p>${esc(m.lore)}</p></section>` : ""}
            <section class="section"><h2>Трапляються разом</h2>${m.seen_together.length ? `<div class="reco">${m.seen_together.map(([id, name, n]) =>
              `<a href="#/monster/${enc(id)}"><b>${esc(name)}</b><span>${fmt(n)} ${plural(n, "спільний бій", "спільні бої", "спільних боїв")}</span></a>`).join("")}</div>`
              : '<p class="muted">У записаних боях цей монстр не траплявся разом з іншими.</p>'}</section>
            <section class="section"><h2>Пригоди з цим монстром</h2>${m.appears_in.length ? `<div class="reco">${m.appears_in.map(([id, title]) =>
              `<a href="#/adventure/${enc(id)}"><b>${esc(title)}</b><span></span></a>`).join("")}</div>` : '<p class="muted">Пригод із цим монстром поки немає.</p>'}</section>
          </div>
        </div>`;
    } catch (error) {
      box.innerHTML = errorState(error);
    }
  },
};

// ================================================================ ХРОНІКИ
const ENCOUNTER_FILTERS = ["q", "command", "min_rounds", "max_rounds"];

VIEWS.chronicles = {
  title: () => "Хроніки боїв",
  html: (t) => {
    const p = t.params;
    return `
      <div class="forge-head"><div><h1 class="gilded">Хроніки боїв</h1>
        <p class="lead">Майже 15 тисяч справжніх боїв із Discord-ігор (датасет FIREBALL): що розповідав ведучий, скільки тривав бій і хто в ньому бився.</p></div></div>
      <form class="slab filters" id="enc-form" autocomplete="off">
        <label class="lab wide">Що відбувалось, англійською<input class="field" name="q" value="${esc(p.get("q") || "")}" placeholder="dragon attacks the village"></label>
        <label class="lab">Команда бота<input class="field" name="command" value="${esc(p.get("command") || "")}" placeholder="init attack"></label>
        <label class="lab short">Раундів від<input class="field" type="number" name="min_rounds" min="0" value="${esc(p.get("min_rounds") || "")}"></label>
        <label class="lab short">Раундів до<input class="field" type="number" name="max_rounds" min="0" value="${esc(p.get("max_rounds") || "")}"></label>
        <label class="check"><input type="checkbox" name="exclude_abandoned" value="false"${p.get("exclude_abandoned") === "false" ? " checked" : ""}>показувати покинуті сесії</label>
        <div class="go"><button class="rune" type="submit"><svg aria-hidden="true"><use href="#i-search"/></svg>Знайти</button><a class="ghost" href="#/chronicles">Скинути</a></div>
      </form>
      <div id="enc-results" class="section"></div>`;
  },
  after(main, t) {
    const form = $("enc-form");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      FX.strike(form.querySelector(".rune"));
      go(`#/chronicles?${formParams(form)}`);
    });
    if (ENCOUNTER_FILTERS.some((k) => t.params.get(k))) runEncounterSearch(t.params);
    else renderChronicleIntro();
  },
};

function mediansHTML(s) {
  const minutes = s.median_duration_minutes;
  const rounds = s.median_rounds;
  return `<div class="carved"><b>${minutes == null ? "—" : fmt1(minutes)}</b><span>${minutes == null ? "хвилин" : unit(minutes, "хвилина", "хвилини", "хвилин", "хвилини")} триває бій</span></div>
    <div class="carved"><b>${rounds == null ? "—" : fmt1(rounds)}</b><span>${rounds == null ? "раундів" : unit(rounds, "раунд", "раунди", "раундів", "раунду")} у бою</span></div>`;
}

async function renderChronicleIntro() {
  const box = $("enc-results");
  box.innerHTML = `<div class="cols"><div class="slab"><h2>Типовий бій</h2><div class="medians" id="enc-medians">${loading}</div>
      <p class="muted small">Медіани без покинутих сесій: середні тут оманливі, бо окремі бої тягнуться тижнями.</p></div>
    <div class="slab"><h2>З чого почати</h2><p class="muted">Шукайте в репліках ведучих сцени, монстрів чи місця дії. Кілька прикладів:</p>
      <div class="omens">${["dragon", "goblin ambush", "tavern", "lich", "owlbear", "pirate ship"].map((q) =>
        `<a class="chip" href="#/chronicles?q=${enc(q)}">${esc(q)}</a>`).join("")}</div></div></div>`;
  try {
    const s = await api("/api/statistics/combat");
    $("enc-medians").innerHTML = mediansHTML(s);
  } catch (error) {
    $("enc-medians").innerHTML = `<div class="error small">${esc(error.message)}</div>`;
  }
}

function encounterHTML(e, index) {
  const started = new Date(e.started_at);
  const facts = [
    e.duration_minutes != null && `${fmt1(e.duration_minutes)} хв`,
    e.rounds_count != null && `${fmt(e.rounds_count)} ${plural(e.rounds_count, "раунд", "раунди", "раундів")}`,
    e.participants_count != null && `${fmt(e.participants_count)} ${plural(e.participants_count, "учасник", "учасники", "учасників")}`,
  ].filter(Boolean);
  const snippets = (e.snippets || []).map((s) => `<p class="snippet">${snippetHTML(s)}</p>`).join("");
  return `<article class="scroll" data-index="${index}">
    <header><h3>${esc(started.toLocaleDateString("uk-UA", { day: "numeric", month: "long", year: "numeric" }))}</h3>
      <div class="meta">${facts.map((f) => `<span class="chip">${esc(f)}</span>`).join("")}${e.is_abandoned ? '<span class="badge empty">покинута сесія</span>' : ""}</div></header>
    ${snippets || (e.narrative_excerpt ? `<p class="snippet">${esc(e.narrative_excerpt)}</p>` : '<p class="muted small">Ведучий у цьому бою нічого не розповідав.</p>')}
    <details><summary>Учасники й команди</summary><div class="full">
      ${e.commands.length ? `<div class="chips">${e.commands.map((c) => `<a class="chip" href="#/chronicles?command=${enc(c)}">${esc(c)}</a>`).join("")}</div>` : ""}
      ${e.player_character_ids.length ? `<div class="chips">${e.player_character_ids.map((id) => `<a class="chip" href="#/character/${enc(id)}" data-character="${esc(id)}">персонаж…</a>`).join("")}</div>`
        : '<span class="muted small">Персонажів гравців не зафіксовано.</span>'}
      <div class="muted small">Ідентифікатор бою: ${esc(e.encounter_id)}</div>
    </div></details></article>`;
}

async function runEncounterSearch(params) {
  const box = $("enc-results");
  box.innerHTML = loading;
  try {
    const result = await api(`/api/search/encounters?${params}`);
    const page = Number(params.get("page") || 1);
    const pages = Math.max(1, Math.ceil(result.total / result.page_size));
    box.innerHTML = `<div class="results-bar"><h2>${result.total ? `Знайдено ${fmt(result.total)} ${plural(result.total, "бій", "бої", "боїв")}` : "Нічого не знайдено"}</h2></div>
      <div class="inscription"><span>час відповіді <b>${fmt(result.latency_ms)} мс</b></span><span>з кешу: <b>${result.cache_hit ? "так" : "ні"}</b></span></div>
      ${result.items.length ? `<div class="scrolls">${result.items.map(encounterHTML).join("")}</div>`
        : emptyState("Таких боїв не знайшлося", "Репліки ведучих англомовні: спробуйте інші слова, наприклад «ambush» чи «dragon».")}
      ${result.total > result.page_size ? `<div class="pager"><button class="ghost" data-page="${page - 1}"${page <= 1 ? " disabled" : ""}>Попередня</button>
        <span>сторінка ${page} з ${fmt(pages)}</span><button class="ghost" data-page="${page + 1}"${page >= pages ? " disabled" : ""}>Наступна</button></div>` : ""}`;
    box.addEventListener("toggle", (event) => {
      const details = event.target;
      if (!details.open || details.dataset.loaded) return;
      details.dataset.loaded = "1";
      details.querySelectorAll("[data-character]").forEach(async (chip) => {
        try {
          const c = await api(`/api/characters/${enc(chip.dataset.character)}`);
          chip.textContent = [c.name, c.char_class && `${c.char_class} ${c.level ?? ""}`.trim()].filter(Boolean).join(", ");
        } catch { chip.textContent = "персонаж"; }
      });
    }, true);
    box.querySelector(".pager")?.addEventListener("click", (e) => {
      const button = e.target.closest("[data-page]");
      if (!button || button.disabled) return;
      const next = new URLSearchParams(params);
      next.set("page", button.dataset.page);
      go(`#/chronicles?${next}`);
    });
  } catch (error) {
    box.innerHTML = errorState(error);
  }
}

// ---------------------------------------------------------------- сторінка персонажа
VIEWS.character = {
  title: () => "Персонаж",
  html: () => `${backButton}<div id="character">${loading}</div>`,
  async after(main, t) {
    const box = $("character");
    try {
      const c = await api(`/api/characters/${enc(t.id)}`);
      document.title = `${c.name} — D&D Adventures`;
      const share = c.hp_max ? Math.max(0, Math.min(1, (c.hp_current ?? 0) / c.hp_max)) : null;
      const dates = c.encounter_ids.map((id) => Number(String(id).split("-")[0])).filter(Boolean).sort((a, b) => a - b);
      box.innerHTML = `
        <div class="profile-head">
          <div><h1 class="gilded">${esc(c.name)}</h1>
            <p class="lead">${esc([c.race, c.classes.map(([cl, lv]) => `${cl} ${lv}`).join(" / ") || c.char_class].filter(Boolean).join(", "))}</p></div>
          ${share != null ? `<div class="hp ${esc(c.hp_status || "")}"><span class="small muted">Хіти в останньому бою: ${fmt(c.hp_current)} з ${fmt(c.hp_max)}${c.hp_status ? `, ${esc(HP_STATUS[c.hp_status] || c.hp_status)}` : ""}</span>
            <div class="bar"><i style="width:${(share * 100).toFixed(1)}%"></i></div></div>` : ""}
        </div>
        <dl class="ledger">
          <div><dt>Рівень</dt><dd>${c.level ?? "—"}</dd></div><div><dt>Мультиклас</dt><dd>${c.is_multiclass ? "так" : "ні"}</dd></div>
          <div><dt>Боїв</dt><dd>${fmt(c.encounters_count)}</dd></div>
          ${dates.length ? `<div><dt>Грав</dt><dd>${esc(new Date(dates[0] * 1000).toLocaleDateString("uk-UA"))} — ${esc(new Date(dates[dates.length - 1] * 1000).toLocaleDateString("uk-UA"))}</dd></div>` : ""}
        </dl>
        <section class="section"><h2>Закляття</h2>${c.spells.length ? `<div class="chips">${c.spells.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div>` : '<p class="muted">Персонаж не користувався закляттями.</p>'}</section>
        ${c.attacks.length ? `<section class="section"><h2>Атаки</h2><div class="chips">${c.attacks.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div></section>` : ""}`;
    } catch (error) {
      box.innerHTML = errorState(error);
    }
  },
};

// ================================================================ ЗАЛА СЛАВИ
function barsHTML(rows) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return rows.map((r) => `<div class="row"><div>${r.label}</div><div class="bar"><i style="width:${((r.value / max) * 100).toFixed(1)}%"></i></div><div class="num">${fmt(r.value)}</div></div>`).join("")
    || '<div class="muted small">Даних ще немає.</div>';
}

VIEWS.halls = {
  title: () => "Зала слави",
  html: () => `
    <div class="forge-head"><div><h1 class="gilded">Зала слави</h1>
      <p class="lead">Що насправді відбувається за ігровим столом: найчастіші монстри, тривалість боїв, улюблені класи й закляття гравців.</p></div></div>
    <section class="slab"><h2>Монстри, яких зустрічають найчастіше</h2><div id="hall-podium">${loading}</div></section>
    <div class="cols section">
      <div class="stack">
      <section class="slab"><h2>Типовий бій</h2><div class="medians" id="hall-combat">${loading}</div>
        <p class="muted small">Медіани без покинутих сесій.</p></section>
      <section class="slab"><h2>Закляття класу</h2>
        <form class="controls" id="hall-spells-form"><select class="field" name="char_class" aria-label="Клас">${CLASSES.map((c) => `<option${c === "Wizard" ? " selected" : ""}>${c}</option>`).join("")}</select>
          <input class="field" type="number" name="level" min="1" max="20" value="5" aria-label="Рівень" style="width:90px">
          <button class="ghost" type="submit">Показати</button></form>
        <div class="bars" id="hall-spells">${loading}</div></section>
      </div>
      <div class="stack">
      <section class="slab"><h2>Склад партій</h2><p class="muted small" style="margin-top:-6px">Скільки персонажів кожного класу грали в записаних боях</p><div class="bars" id="hall-party">${loading}</div></section>
      <section class="slab"><h2>Класи й раси</h2><div class="table-scroll"><table><thead><tr><th>Клас</th><th>Раса</th><th class="num">Персонажів</th></tr></thead>
        <tbody id="hall-classrace"></tbody></table></div></section>
      </div>
    </div>`,
  after() {
    api("/api/statistics/monsters?limit=15").then((s) => {
      const list = s.popular_monsters;
      if (!list.length) {
        $("hall-podium").innerHTML = '<p class="muted">Статистики ще немає: завантажте CSV у кузні даних.</p>';
        return;
      }
      const ped = (entry, place) => entry ? `<a class="pedestal p${place}" href="#/monster/${enc(entry[0])}"><span class="crest">${esc(entry[1])}</span>
        <span class="count">${fmt(entry[2])} ${plural(entry[2], "поява", "появи", "появ")}</span><span class="stone">${place}</span></a>` : "<span></span>";
      const max = Math.max(1, ...list.map((m) => m[2]));
      $("hall-podium").innerHTML = `<div class="podium">${ped(list[1], 2)}${ped(list[0], 1)}${ped(list[2], 3)}</div>
        <ol class="ranks">${list.slice(3).map(([id, name, n]) => `<li><a href="#/monster/${enc(id)}">${esc(name)}</a>
          <div class="bar"><i style="width:${((n / max) * 100).toFixed(1)}%"></i></div><span class="num">${fmt(n)}</span></li>`).join("")}</ol>`;
    }).catch((e) => ($("hall-podium").innerHTML = `<div class="error">${esc(e.message)}</div>`));
    api("/api/statistics/combat").then((s) => {
      $("hall-combat").innerHTML = mediansHTML(s);
    }).catch((e) => ($("hall-combat").innerHTML = `<div class="error">${esc(e.message)}</div>`));
    api("/api/statistics/party?limit=12").then((s) => {
      $("hall-party").innerHTML = barsHTML(s.party_composition.slice(0, 12).map(([cls, n]) => ({ label: esc(cls ?? "—"), value: n })));
      $("hall-classrace").innerHTML = s.class_race.slice(0, 12).map(([cls, race, n]) => `<tr><td>${esc(cls ?? "—")}</td><td>${esc(race ?? "—")}</td><td class="num">${fmt(n)}</td></tr>`).join("")
        || '<tr><td colspan="3" class="muted">Даних ще немає.</td></tr>';
    }).catch((e) => ($("hall-party").innerHTML = `<div class="error">${esc(e.message)}</div>`));
    const form = $("hall-spells-form");
    const load = async () => {
      const params = new URLSearchParams({ char_class: form.elements.char_class.value, level: form.elements.level.value || "5", limit: "12" });
      try {
        const s = await api(`/api/statistics/spells?${params}`);
        $("hall-spells").innerHTML = barsHTML(s.typical_spells.map(([spell, n]) => ({ label: esc(spell), value: n })));
      } catch (e) {
        $("hall-spells").innerHTML = `<div class="error">${esc(e.message)}</div>`;
      }
    };
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      load();
    });
    load();
  },
};

// ================================================================ АТЛАС (аналітика ніш, група запитів 11)
// Звіт рахується по всій шардованій колекції пригод: кожен шард групує свою
// зону ключів (map), застосунок зводить часткові результати (reduce), а
// вбудована агрегація через mongos на тому самому знімку слугує звіркою.
const DIMENSIONS = { tag: "теги", system: "системи", setting: "ігрові світи", tier: "рівні гри", year: "роки видання" };
const SCOPES = { all: "увесь каталог", collected: "модулі itch.io", generated: "згенеровані пригоди" };
const TIERS = { 1: "рівні 1–4", 2: "рівні 5–10", 3: "рівні 11–16", 4: "рівні 17–20" };
const ZONE_COLORS = ["#ff6a1a", "#f2c35b", "#9db8ff", "#9ccf7f", "#e9a08f", "#c9a6ff"];
const ATLAS_DEFAULTS = { dimension: "tag", scope: "all", order: "count", limit: "20" };
const atlas = { chart: null, seq: 0 };

function nicheName(dimension, key) {
  if (key === "") return "не вказано";
  if (dimension === "setting") return settingName(key) || key;
  if (dimension === "tier") return TIERS[key] || key;
  return key;
}

const ms = (n) => (n == null ? "—" : `${n < 10 ? fmt1(n) : fmt(n)} мс`);
const stamp = (iso) => (iso ? new Date(iso).toLocaleString("uk-UA", { dateStyle: "short", timeStyle: "medium" }) : "—");

VIEWS.atlas = {
  title: () => "Атлас ніш",
  html: (t) => {
    const p = t.params;
    const value = (name) => p.get(name) || ATLAS_DEFAULTS[name];
    const select = (name, options) => `<select class="field" name="${name}">${Object.entries(options).map(([v, l]) =>
      `<option value="${v}"${value(name) === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    return `
      <div class="forge-head"><div><h1 class="gilded">Атлас ніш</h1>
        <p class="lead">Скільки модулів має кожна тема, як їх оцінюють, скільки вони коштують і тривають — по всьому каталогу.
          Кожен шард рахує свою зону ключів, а застосунок зводить часткові результати (map-reduce).</p></div></div>
      <form class="slab filters" id="atlas-form" autocomplete="off">
        <label class="lab">Групувати за${select("dimension", DIMENSIONS)}</label>
        <label class="lab">Частина каталогу${select("scope", SCOPES)}</label>
        <label class="lab">Порядок${select("order", { count: "за кількістю пригод", rating: "за середньою оцінкою" })}</label>
        <label class="lab short">Ніш у звіті${select("limit", { 10: "10", 20: "20", 50: "50", 100: "100" })}</label>
        <div class="go"><button class="rune" type="submit">Показати</button>
          <button class="ghost" type="button" id="atlas-refresh" title="Обчислити заново, не беручи результат із кешу">Перерахувати</button></div>
      </form>
      <section class="slab section"><h2>Як зібрано звіт</h2><div id="atlas-run">${loading}</div></section>
      <section class="slab section"><h2>Внесок шардів у верхні ніші</h2>
        <div class="legend" id="atlas-legend"></div>
        <div class="chartbox atlas-chart"><canvas id="atlas-chart" aria-label="Кількість пригод ніші за шардами"></canvas></div></section>
      <section class="slab section"><h2>Ніші</h2>
        <div class="table-scroll"><table class="atlas-table"><thead><tr><th class="num">№</th><th>Ніша</th><th class="num">Пригод</th>
          <th class="num">Частка</th><th class="num">Оцінка</th><th class="num">Медіана ≈</th><th class="num">Оцінено</th>
          <th class="num">Відгуків</th><th class="num">Безкоштовних</th><th class="num">Ціна</th><th class="num">Тривалість</th>
          <th>Роки</th><th class="num">З itch.io</th></tr></thead><tbody id="atlas-rows"></tbody></table></div>
        <p class="muted small">Частка — від пригод, що потрапили в запит; пригода з кількома тегами входить у кожну їхню нішу.
          Середні оцінка, ціна (платних, у доларах) і тривалість — алгебраїчні показники: їх зведено точно із сум і кількостей шардів.
          Медіана — холістичний показник, її оцінено за гістограмою оцінок із кроком 0,5 бала. У порядку за оцінкою ніші,
          де оцінено менше ніж 5 пригод, ідуть після решти.</p></section>`;
  },
  after(main, t) {
    const form = $("atlas-form");
    const params = () => {
      const query = new URLSearchParams();
      for (const name of Object.keys(ATLAS_DEFAULTS)) query.set(name, form.elements[name].value);
      return query;
    };
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      FX.strike(form.querySelector(".rune"));
      go(`#/atlas?${params()}`);
    });
    form.addEventListener("change", () => go(`#/atlas?${params()}`));
    $("atlas-refresh").addEventListener("click", (e) => {
      FX.strike(e.currentTarget);
      loadAtlas(params(), true);
    });
    loadAtlas(params(), false);
  },
};

async function loadAtlas(params, refresh) {
  const seq = ++atlas.seq;
  $("atlas-run").innerHTML = '<div class="muted">Шарди рахують свої зони…</div>';
  if (refresh) params.set("refresh", "true");
  try {
    const report = await api(`/api/analytics/niches?${params}`);
    if (seq !== atlas.seq || !$("atlas-run")) return;
    renderAtlas(report);
  } catch (error) {
    if (seq === atlas.seq && $("atlas-run")) $("atlas-run").innerHTML = `<div class="error">Звіт не обчислено: ${esc(error.message)}</div>`;
  }
}

function renderAtlas(r) {
  const zones = r.zones || [];
  const answered = zones.filter((z) => !z.error).length;
  const speedup = r.map_max_ms ? r.map_sum_ms / r.map_max_ms : null;
  const m = r.merged || {};
  const stages = (list) => (list && list.length ? list.map(esc).join(" → ") : "—");
  const verdict = m.matches == null ? '<span class="badge empty">не звірено</span>'
    : m.matches ? '<span class="badge ok">збігається</span>' : '<span class="badge empty">не збігається</span>';
  $("atlas-run").innerHTML = `
    <div class="mr">
      <p class="mr-note">${r.snapshot ? `Усі зони прочитано на одному знімку даних${r.snapshot_at ? ` — <b>${esc(stamp(r.snapshot_at))}</b>` : ""}
        <span class="muted">(atClusterTime ${esc(r.snapshot)})</span>: нові пригоди, записані під час підрахунку, не потрапляють у жодну зону.`
        : "Сховище не підтримує читання snapshot: кожна зона прочитана в момент свого запиту."}
        ${r.cache_hit ? ` <span class="badge gen">з кешу, обчислено ${esc(stamp(r.generated_at))}</span>` : ""}</p>
      <div class="mr-zones">${zones.map((z, i) => `
        <div class="tablet mr-zone${z.error ? " failed" : ""}" style="--zone:${ZONE_COLORS[i % ZONE_COLORS.length]}">
          <div class="l">map · зона ${z.index + 1} · ключі ${esc(z.label)}</div>
          <div class="v">${z.error ? "недоступна" : esc(z.shard || "один вузол")}</div>
          ${z.error ? `<div class="s warn">${esc(z.error)}</div>`
            : `<div class="s">${fmt(z.documents)} ${plural(z.documents, "пригода", "пригоди", "пригод")} → ${fmt(z.groups)} ${plural(z.groups, "група", "групи", "груп")}</div>
               <div class="s hot">${ms(z.elapsed_ms)} на шарді</div>`}
        </div>`).join("")}</div>
      <div class="mr-arrow" aria-hidden="true">↓ назад передаються лише часткові агрегати груп, а не документи ↓</div>
      <div class="tablet key mr-reduce">
        <div class="l">reduce у застосунку</div>
        <div class="v">${fmt(r.groups_total)} ${plural(r.groups_total, "ніша", "ніші", "ніш")} · ${fmt(r.documents)} ${plural(r.documents, "пригода", "пригоди", "пригод")}</div>
        <div class="s">зведено ${answered} з ${zones.length} часткових результатів за ${ms(r.reduce_ms)}</div>
      </div>
      <div class="inscription mr-times"><span>паралельно <b>${ms(r.map_max_ms)}</b></span>
        <span>послідовно було б <b>${ms(r.map_sum_ms)}</b>${speedup ? ` (×${fmt1(speedup)})` : ""}</span>
        <span>увесь збір <b>${ms(r.gather_ms)}</b></span></div>
      <div class="mr-check">${m.error ? `<span class="badge empty">недоступна</span> Вбудована агрегація: ${esc(m.error)}`
        : `${verdict} Той самий запит однією агрегацією через mongos: <b>${ms(m.elapsed_ms)}</b>, ${fmt(m.groups)} груп, ${fmt(m.documents)} пригод.
          <br><span class="muted small">Маршрутизатор сам поділив конвеєр: на шардах (${esc((m.shards || []).join(", ") || "—")}) — ${stages(m.shards_part)};
          злиття${m.merge_type ? ` (${esc(m.merge_type)})` : ""} — ${stages(m.merger_part)}.</span>`}</div>
      ${r.complete ? "" : `<div class="error">Неповний звіт: відповіли ${answered} з ${zones.length} зон (≈ ${pct(r.coverage)} каталогу).
        Ключ розподілу — хеш, тож кожна зона є рівномірною вибіркою каталогу: частки й середні — оцінки за вибіркою.
        Неповний звіт не кешується.</div>`}
    </div>`;

  const rows = r.rows || [];
  $("atlas-rows").innerHTML = rows.map((row, i) => `<tr>
      <td class="num">${i + 1}</td>
      <td class="q">${row.key === "" ? `<span class="muted">${esc(nicheName(r.dimension, row.key))}</span>` : esc(nicheName(r.dimension, row.key))}</td>
      <td class="num">${fmt(row.adventures)}</td><td class="num">${pct(row.share)}</td>
      <td class="num">${row.avg_rating == null ? "—" : fmt1(row.avg_rating)}</td><td class="num">${row.median_rating == null ? "—" : fmt1(row.median_rating)}</td>
      <td class="num">${fmt(row.rated)}</td><td class="num">${fmt(row.reviews_total)}</td>
      <td class="num">${pct(row.free_share)}</td><td class="num">${row.avg_price == null ? "—" : USD.format(row.avg_price)}</td>
      <td class="num">${row.avg_duration == null ? "—" : `${fmt1(row.avg_duration)} год`}</td>
      <td>${row.year_min == null ? "—" : row.year_min === row.year_max ? row.year_min : `${row.year_min}–${row.year_max}`}</td>
      <td class="num">${pct(row.collected_share)}</td></tr>`).join("")
    || '<tr><td colspan="13" class="muted">Пригод ще немає: запустіть генератор або завантажте CSV у кузні даних.</td></tr>';

  $("atlas-legend").innerHTML = zones.map((z, i) => `<span><i style="border-color:${ZONE_COLORS[i % ZONE_COLORS.length]}"></i>${esc(z.shard || `зона ${z.index + 1}`)} · ключі ${esc(z.label)}</span>`).join("");
  drawAtlasChart(r, rows.slice(0, 15), zones);
}

function drawAtlasChart(r, rows, zones) {
  if (!window.Chart || !$("atlas-chart")) return;
  atlas.chart?.destroy();
  atlas.chart = new window.Chart($("atlas-chart"), {
    type: "bar",
    data: {
      labels: rows.map((row) => nicheName(r.dimension, row.key)),
      datasets: zones.map((z, i) => ({
        label: z.shard || `зона ${z.index + 1}`,
        data: rows.map((row) => (row.by_zone || [])[i] || 0),
        backgroundColor: ZONE_COLORS[i % ZONE_COLORS.length],
        borderWidth: 0,
        barPercentage: 0.8,
      })),
    },
    options: {
      indexAxis: "y", locale: "uk-UA", animation: false, responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { stacked: true, beginAtZero: true, ticks: { color: "#a8917c", font: { family: "Alegreya Sans" } }, grid: { color: "rgba(59,40,27,.6)" }, border: { color: "#3b281b" } },
        y: { stacked: true, ticks: { color: "#d8c6ae", font: { family: "Alegreya Sans", size: 13 } }, grid: { display: false }, border: { color: "#3b281b" } },
      },
    },
  });
}

// ================================================================ КУЗНЯ (моніторинг)
const RATE_MIN = 10;
const RATE_MAX = 5000;
const sliderToRate = (v) => Math.round(RATE_MIN * Math.pow(RATE_MAX / RATE_MIN, v / 1000));
const rateToSlider = (r) => Math.round((1000 * Math.log(Math.max(RATE_MIN, r) / RATE_MIN)) / Math.log(RATE_MAX / RATE_MIN));
const live = { last: null, at: 0, editing: false, dbSamples: [], series: { labels: [], target: [], generated: [], saved: [], db: [] }, chart: null, lava: null };
const DB_WINDOW_SECONDS = 5;

VIEWS.forge = {
  title: () => "Кузня даних",
  html: () => `
    <div class="forge-head"><div><h1 class="gilded">Кузня даних</h1>
      <p class="lead">Моніторинг збору й запису в реальному часі: потік стану оновлюється щосекунди.</p></div></div>
    <section class="slab">
      <h2>Цільова швидкість генерації</h2>
      <div class="forge-rate">
        <div>
          <div class="lava" id="lava"><canvas aria-hidden="true"></canvas><div class="lava-groove"><div class="lava-fill"></div></div>
            <input type="range" id="rate-slider" min="0" max="1000" step="1" aria-label="Цільова швидкість генерації, документів за секунду"></div>
          <div class="ticks" id="rate-ticks"></div>
        </div>
        <div class="rate-read" id="rate-read"><b id="rate-actual">—</b><span>згенеровано за секунду</span></div>
        <div class="rate-controls">
          <input class="field" id="rate-input" type="number" min="10" max="5000" step="1" aria-label="Цільова швидкість, документів за секунду">
          <span class="muted">документів за секунду</span>
          <button class="rune" id="rate-apply" type="button">Застосувати</button>
          <span class="error" id="rate-error" role="alert"></span>
        </div>
        <p class="rate-note" id="rate-note">Екземплярів генератора: —. Нова швидкість діє з наступного пакета, не пізніше ніж за секунду.</p>
      </div>
    </section>
    <div class="tablets">
      <div class="tablet"><div class="l">Переглянуто краулером</div><div class="v" id="k-crawled">0</div><div class="s" id="k-crawled-s">0 стор/с</div></div>
      <div class="tablet"><div class="l">Завантажено з CSV</div><div class="v" id="k-csv">0</div><div class="s" id="k-csv-s">рядків</div></div>
      <div class="tablet"><div class="l">Згенеровано</div><div class="v" id="k-gen">0</div><div class="s hot" id="k-gen-s">0 док/с</div></div>
      <div class="tablet key"><div class="l">Збережено в БД</div><div class="v" id="k-saved">0</div><div class="s hot" id="k-saved-s">0 док/с</div></div>
      <div class="tablet"><div class="l">Відхилено</div><div class="v" id="k-rejected">0</div><div class="s">журнал браку</div></div>
      <div class="tablet"><div class="l">Черга пакетів</div><div class="v" id="k-queue">0 / 50</div><div class="s" id="k-queue-s">зворотний тиск не діє</div></div>
    </div>
    <div class="cols">
      <section class="slab"><h2>Навантаження за 60 секунд</h2>
        <div class="legend"><span><i style="border-color:#f2c35b;border-top-style:dashed"></i>цільова</span><span><i style="border-color:#9db8ff"></i>згенеровано</span>
          <span><i style="border-color:#ff6a1a"></i>збережено</span><span><i style="border-color:#9ccf7f;border-top-style:dotted"></i>приріст у MongoDB</span></div>
        <div class="chartbox"><canvas id="load-chart" aria-label="Графік навантаження, документів за секунду"></canvas></div></section>
      <section class="slab"><h2>MongoDB: пригоди на шардах</h2>
        <div id="shards"><div class="muted small">Даних ще немає.</div></div>
        <div class="dbline">Усього в колекції: <b id="db-total">0</b><br>Приріст за даними БД: <b id="db-rate">0 док/с</b><br>
          Відхилення шардів від середнього: <b id="db-dev">—</b><br><span class="muted small">Підрахунок $collStats, кешується на 1 с.</span></div></section>
    </div>
    <div class="cols section">
      <section class="slab"><h2>Краулер itch.io</h2>
        <div class="crawl">
          <div><span>переглянуто сторінок</span><b id="cr-pages">0</b></div><div><span>за секунду</span><b id="cr-rate">0</b></div>
          <div><span>помилок і тайм-аутів</span><b id="cr-errors">0</b></div><div><span>відповідей 429/503</span><b id="cr-throttled">0</b></div>
          <div><span>заборонено robots.txt</span><b id="cr-robots">0</b></div><div><span>карток розібрано</span><b id="cr-cards">0</b></div>
        </div>
        <p class="muted small" id="cr-stage">Затримка між запитами 1,5–3 с.</p></section>
      <section class="slab"><h2>Заповненість полів пригод</h2>
        <table><thead><tr><th>Поле</th><th class="num">зібрані</th><th class="num">згенеровані</th></tr></thead><tbody id="fill"></tbody></table></section>
    </div>
    <section class="slab section"><h2>Останні пошукові запити</h2>
      <div class="qstats"><span>частка влучань у кеш <b id="q-hit">—</b></span><span>медіана затримки <b id="q-p50">—</b></span><span>95-й процентиль <b id="q-p95">—</b></span></div>
      <div class="table-scroll"><table><thead><tr><th>Час</th><th>Вид</th><th>Запит</th><th>Статус</th><th class="num">Результатів</th><th class="num">Затримка</th><th>Кеш</th></tr></thead>
        <tbody id="queries"></tbody></table></div></section>`,
  after() {
    $("rate-ticks").innerHTML = [10, 50, 100, 500, 1000, 5000].map((t) => `<span style="left:${(rateToSlider(t) / 10).toFixed(2)}%">${fmt(t)}</span>`).join("");
    const slider = $("rate-slider");
    const input = $("rate-input");
    live.lava = FX.lava($("lava"));
    slider.addEventListener("input", () => {
      live.editing = true;
      input.value = sliderToRate(Number(slider.value));
    });
    input.addEventListener("input", () => {
      live.editing = true;
      const value = Number(input.value);
      if (value >= RATE_MIN && value <= RATE_MAX) {
        slider.value = rateToSlider(value);
        live.lava.set();
      }
    });
    slider.addEventListener("change", () => applyRate(true));
    $("rate-apply").addEventListener("click", () => applyRate(false));
    input.addEventListener("keydown", (e) => e.key === "Enter" && applyRate(false));
    api("/api/rate").then((r) => showRate(r.documents_per_second)).catch(() => {});
    initChart();
    if (live.last) renderForge(live.last);
  },
};

function showRate(rate) {
  if (!$("rate-input")) return;
  $("rate-input").value = rate;
  $("rate-slider").value = rateToSlider(rate);
  live.lava?.set();
}

async function applyRate(fromSlider) {
  const rate = Number($("rate-input").value);
  $("rate-error").textContent = "";
  const slider = $("rate-slider");
  const box = slider.getBoundingClientRect();
  const p = Number(slider.value) / 1000;
  FX.strike(fromSlider ? { x: box.left + 22 + (box.width - 44) * p, y: box.top + box.height / 2 } : $("rate-apply"));
  try {
    const result = await api("/api/rate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ documents_per_second: rate }) });
    showRate(result.documents_per_second);
  } catch (error) {
    $("rate-error").textContent = `Швидкість не змінено: ${error.message}`;
  }
  live.editing = false;
}

function initChart() {
  if (!window.Chart || !$("load-chart")) return;
  live.chart?.destroy();
  const dataset = (label, color, dash, width = 2.2) => ({ label, data: [], borderColor: color, borderWidth: width, borderDash: dash, pointRadius: 0, tension: 0.3 });
  live.chart = new window.Chart($("load-chart"), {
    type: "line",
    data: { labels: live.series.labels, datasets: [dataset("цільова", "#f2c35b", [7, 5], 1.8), dataset("згенеровано", "#9db8ff"),
      dataset("збережено", "#ff6a1a", undefined, 2.6), dataset("приріст у MongoDB", "#9ccf7f", [2, 4])] },
    options: {
      locale: "uk-UA", animation: false, responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 5, color: "#a8917c", font: { family: "Alegreya Sans" } }, grid: { display: false }, border: { color: "#3b281b" } },
        y: { beginAtZero: true, ticks: { color: "#a8917c", font: { family: "Alegreya Sans" } }, grid: { color: "rgba(59,40,27,.6)" }, border: { color: "#3b281b" } },
      },
    },
  });
  syncChart();
}
function syncChart() {
  if (!live.chart) return;
  const s = live.series;
  live.chart.data.labels = s.labels;
  [s.target, s.generated, s.saved, s.db].forEach((data, i) => (live.chart.data.datasets[i].data = data));
  live.chart.update("none");
}

const sumBy = (object, prefix) => Object.entries(object || {}).filter(([k]) => k.startsWith(prefix)).reduce((a, [, v]) => a + v, 0);
function zoneLabels(count) {
  const lows = Array.from({ length: count }, (_, i) => Math.ceil((i * 1024) / count));
  return lows.map((low, i) => `ключі ${String(low).padStart(4, "0")}–${String((lows[i + 1] ?? 1024) - 1).padStart(4, "0")}`);
}

// Знімок потоку SSE: історію графіка й приріст за даними БД ведемо завжди,
// а сторінку кузні оновлюємо, лише коли вона відкрита
function onStats(s) {
  live.last = s;
  live.at = Date.now();
  const r = s.rates || {};
  const now = Date.now() / 1000;
  const samples = live.dbSamples;
  if (!samples.length || s.adventures_total !== samples[samples.length - 1].total) samples.push({ total: s.adventures_total, time: now });
  while (samples.length > 2 && now - samples[0].time > DB_WINDOW_SECONDS) samples.shift();
  const first = samples[0];
  const newest = samples[samples.length - 1];
  s.dbRate = now - newest.time <= 3 && newest.time - first.time >= 2 ? Math.max(0, (newest.total - first.total) / (newest.time - first.time)) : 0;
  const series = live.series;
  series.labels.push(new Date(s.generated_at).toLocaleTimeString("uk-UA"));
  series.target.push(s.target_rate);
  series.generated.push(r["generator.generated"] || 0);
  series.saved.push(r["ingest.saved.adventure"] || 0);
  series.db.push(s.dbRate);
  if (series.labels.length > 60) Object.values(series).forEach((list) => list.shift());
  const indicator = $("live");
  indicator.classList.remove("off", "beat");
  void indicator.offsetWidth;
  indicator.classList.add("beat");
  if (current.section === "forge" && $("k-saved")) renderForge(s);
}

function renderForge(s) {
  const c = s.counters || {};
  const r = s.rates || {};
  const instances = s.generator_instances || 0;
  if (!live.editing && document.activeElement !== $("rate-input")) showRate(s.target_rate);
  $("rate-actual").textContent = fmt(r["generator.generated"] || 0);
  const share = instances ? s.target_rate / instances : null;
  $("rate-note").textContent = `Екземплярів генератора: ${instances || "—"}${share == null ? "" : `, кожен дає ${fmt(share)} док/с`}. Нова швидкість діє з наступного пакета, не пізніше ніж за секунду.`;

  $("cr-pages").textContent = fmt(c["crawler.pages_ok"] || 0);
  $("cr-rate").textContent = fmt1(r["crawler.pages_ok"] || 0);
  $("cr-errors").textContent = fmt(c["crawler.http_errors"] || 0);
  $("cr-throttled").textContent = fmt(c["crawler.throttled"] || 0);
  $("cr-robots").textContent = fmt(c["crawler.robots_skipped"] || 0);
  $("cr-cards").textContent = fmt(c["crawler.cards_parsed"] || 0);
  const stage = s.crawler_stage;
  $("cr-stage").textContent = `Затримка між запитами 1,5–3 с. ${stage ? `Зараз: ${STAGE_LABELS[stage.stage] || stage.stage}, ${fmt(stage.done)} з ~${fmt(stage.total)}.` : "Обхід не триває."}`;

  $("k-crawled").textContent = fmt(c["crawler.pages_ok"] || 0);
  $("k-crawled-s").textContent = `${fmt1(r["crawler.pages_ok"] || 0)} стор/с`;
  $("k-csv").textContent = fmt(c["csv.rows"] || 0);
  $("k-csv-s").textContent = r["csv.rows"] ? `${fmt(r["csv.rows"])} рядків/с` : c["csv.rows"] ? "рядків, завантаження завершено" : "рядків";
  $("k-gen").textContent = fmt(c["generator.generated"] || 0);
  $("k-gen-s").textContent = `${fmt(r["generator.generated"] || 0)} док/с`;
  $("k-saved").textContent = fmt(sumBy(c, "ingest.saved."));
  $("k-saved-s").textContent = `${fmt(sumBy(r, "ingest.saved."))} док/с`;
  $("k-rejected").textContent = fmt(sumBy(c, "ingest.rejected."));
  $("k-queue").textContent = `${fmt(s.queue_depth)} / ${fmt(s.queue_limit)}`;
  const pressure = (r["queue.backpressure"] || 0) > 0 || s.queue_depth >= s.queue_limit;
  $("k-queue-s").textContent = pressure ? "зворотний тиск діє" : "зворотний тиск не діє";
  $("k-queue-s").className = pressure ? "s warn" : "s";

  const names = Object.keys(s.shards || {}).sort();
  const labels = zoneLabels(names.length);
  const max = Math.max(1, ...names.map((n) => s.shards[n]));
  $("shards").innerHTML = names.length ? names.map((n, i) => `<div class="shard"><div class="sname">${esc(n)}<small>${names.length > 1 ? labels[i] : "один вузол"}</small></div>
      <div class="crucible"><i style="width:${((s.shards[n] / max) * 100).toFixed(1)}%"></i></div><div class="num">${fmt(s.shards[n])}</div></div>`).join("")
    : '<div class="muted small">Даних ще немає.</div>';
  $("db-total").textContent = fmt(s.adventures_total);
  $("db-rate").textContent = `${fmt(s.dbRate)} док/с`;
  const mean = s.adventures_total / Math.max(1, names.length);
  const deviation = names.length > 1 && mean > 0 ? Math.max(...names.map((n) => Math.abs(s.shards[n] - mean) / mean)) : null;
  $("db-dev").textContent = deviation == null ? "—" : `±${fmt1(deviation * 100)} %`;
  syncChart();

  $("q-hit").textContent = pct(s.cache_hit_ratio);
  $("q-p50").textContent = s.latency_p50_ms == null ? "—" : `${fmt(s.latency_p50_ms)} мс`;
  $("q-p95").textContent = s.latency_p95_ms == null ? "—" : `${fmt(s.latency_p95_ms)} мс`;
  $("queries").innerHTML = (s.recent_queries || []).map((q) => `<tr>
      <td>${esc(new Date(q.created_at).toLocaleTimeString("uk-UA"))}</td><td>${esc(KIND_LABELS[q.kind] || q.kind)}</td>
      <td class="q">${esc(q.raw_query) || '<span class="muted">лише фільтри</span>'}</td>
      <td><span class="badge ${q.status === "FOUND" ? "ok" : "empty"}">${q.status === "FOUND" ? "знайдено" : "порожньо"}</span></td>
      <td class="num">${fmt(q.results_count)}</td><td class="num">${fmt(q.latency_ms)} мс</td><td>${q.cache_hit ? "так" : "ні"}</td></tr>`).join("")
    || '<tr><td colspan="7" class="muted">Запитів ще не було.</td></tr>';
  $("fill").innerHTML = Object.entries(s.fill || {}).map(([field, v]) =>
    `<tr><td>${esc(FILL_LABELS[field] || field)}</td><td class="num">${pct(v.collected)}</td><td class="num">${pct(v.generated)}</td></tr>`).join("");
}

function connectStream() {
  const indicator = $("live");
  const label = indicator.querySelector("span");
  const source = new EventSource("/api/stats/stream");
  source.onmessage = (event) => onStats(JSON.parse(event.data));
  source.addEventListener("error", (event) => {
    indicator.classList.add("off");
    label.textContent = event.data ? `потік стану: ${JSON.parse(event.data).detail}` : "потік стану: перепідключення…";
  });
  setInterval(() => {
    if (!live.at || indicator.classList.contains("off")) return;
    const seconds = Math.round((Date.now() - live.at) / 1000);
    label.textContent = seconds <= 1 ? "потік стану: наживо" : `потік стану: ${seconds} с тому`;
  }, 1000);
}

// ---------------------------------------------------------------- старт
(async function start() {
  FX.init();
  await loadReference();
  connectStream();
  route();
})();
