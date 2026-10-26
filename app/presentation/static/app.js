// Веб-інтерфейс D&D Adventures (п. 4.9): три вкладки — «Моніторинг», «Пошук» і
// «Статистика гри». Дані надходять двома каналами: REST-маршрутами (таблиця 4.15)
// і потоком SSE /api/stats/stream, що раз на секунду надсилає знімок стану системи.
"use strict";

const $ = (id) => document.getElementById(id);
const NF = new Intl.NumberFormat("uk-UA");
const NF1 = new Intl.NumberFormat("uk-UA", { maximumFractionDigits: 1 });
const fmt = (n) => (n == null || Number.isNaN(n) ? "—" : NF.format(Math.round(n)));
const fmt1 = (n) => (n == null || Number.isNaN(n) ? "—" : NF1.format(n));
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)} %`);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const KIND_LABELS = { adventure: "пригоди", monster: "монстри", encounter: "зустрічі" };
const STAGE_LABELS = { lists: "сторінки списку", cards: "картки модулів", authors: "профілі авторів", tags: "сторінки тегів" };
const FILL_LABELS = {
  level_min: "рівень персонажів", players_min: "кількість гравців", duration_hours: "тривалість", price: "ціна",
  publication_language: "мова видання", published_year: "рік видання", synopsis: "синопсис", system: "система",
  genre: "жанр", rating_avg: "рейтинг", reviews: "відгуки",
};
const ENVIRONMENTS = ["arctic", "coastal", "desert", "forest", "graveyard", "grassland", "hill", "mountain", "swamp", "underdark", "underwater", "urban"];
const CREATURE_TYPES = ["aberration", "beast", "celestial", "construct", "dragon", "elemental", "fey", "fiend", "giant", "humanoid", "monstrosity", "ooze", "plant", "undead"];
const CLASSES = ["Artificer", "Barbarian", "Bard", "Blood Hunter", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard"];

// Списки вручну (app/data/) — для фільтрів і назв світів
const REF = { settings: {}, systems: [] };

async function getJSON(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const reasons = body.reasons ? `: ${body.reasons.join("; ")}` : body.detail ? `: ${body.detail}` : "";
    throw new Error(`${response.status}${reasons}`);
  }
  return body;
}

async function loadReference() {
  try {
    const [settings, systems] = await Promise.all([getJSON("/static/data/settings.json"), getJSON("/static/data/systems.json")]);
    settings.forEach((s) => (REF.settings[s.setting_id] = s));
    REF.systems = systems;
    $("f-setting").insertAdjacentHTML("beforeend", settings.map((s) => `<option value="${esc(s.setting_id)}">${esc(s.name)}</option>`).join(""));
    const names = [...new Set(systems.map((s) => s.system))];
    $("f-system").insertAdjacentHTML("beforeend", names.map((n) => `<option>${esc(n)}</option>`).join(""));
  } catch (error) {
    console.warn("списки вручну недоступні", error);
  }
  const typeSelect = document.querySelector('#filters-monsters select[name="creature_type"]');
  typeSelect.insertAdjacentHTML("beforeend", CREATURE_TYPES.map((t) => `<option>${t}</option>`).join(""));
  const envSelect = document.querySelector('#filters-monsters select[name="environment"]');
  envSelect.insertAdjacentHTML("beforeend", ENVIRONMENTS.map((e) => `<option>${e}</option>`).join(""));
  $("st-class").innerHTML = CLASSES.map((c) => `<option${c === "Wizard" ? " selected" : ""}>${c}</option>`).join("");
}

// ---------------------------------------------------------------- вкладки

let statsLoaded = false;
function showTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("on", p.id === `tab-${name}`));
  if (name === "stats" && !statsLoaded) {
    statsLoaded = true;
    loadStatistics();
  }
}
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---------------------------------------------------------------- моніторинг

const RATE_MIN = 10;
const RATE_MAX = 5000;
const sliderToRate = (v) => Math.round(RATE_MIN * Math.pow(RATE_MAX / RATE_MIN, v / 1000));
const rateToSlider = (r) => Math.round((1000 * Math.log(Math.max(RATE_MIN, r) / RATE_MIN)) / Math.log(RATE_MAX / RATE_MIN));
let editingRate = false;

function initRateControl() {
  $("rate-ticks").innerHTML = [10, 50, 100, 500, 1000, 5000]
    .map((t) => `<span style="left:${(rateToSlider(t) / 10).toFixed(2)}%">${fmt(t)}</span>`)
    .join("");
  $("rate-slider").addEventListener("input", (e) => {
    editingRate = true;
    $("rate-input").value = sliderToRate(Number(e.target.value));
  });
  $("rate-input").addEventListener("input", (e) => {
    editingRate = true;
    const value = Number(e.target.value);
    if (value >= RATE_MIN && value <= RATE_MAX) $("rate-slider").value = rateToSlider(value);
  });
  $("rate-apply").addEventListener("click", applyRate);
  $("rate-slider").addEventListener("change", applyRate);
  getJSON("/api/rate").then((r) => showRate(r.documents_per_second)).catch(() => {});
}

function showRate(rate) {
  $("rate-input").value = rate;
  $("rate-slider").value = rateToSlider(rate);
}

async function applyRate() {
  const rate = Number($("rate-input").value);
  $("rate-error").textContent = "";
  try {
    const result = await getJSON("/api/rate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ documents_per_second: rate }),
    });
    showRate(result.documents_per_second);
  } catch (error) {
    $("rate-error").textContent = `не застосовано (${error.message})`;
  }
  editingRate = false;
}

const series = { labels: [], target: [], generated: [], saved: [], db: [] };
let chart = null;

function initChart() {
  if (!window.Chart) return;
  const dataset = (label, color, dash) => ({ label, data: [], borderColor: color, borderWidth: 2.2, borderDash: dash, pointRadius: 0, tension: 0.25 });
  chart = new window.Chart($("load-chart"), {
    type: "line",
    data: {
      labels: series.labels,
      datasets: [
        dataset("цільова", "#111111", [7, 5]),
        dataset("згенеровано", "#2f6fde"),
        dataset("збережено (лічильник)", "#1b9e5a"),
        dataset("приріст у MongoDB", "#c2410c", [2, 4]),
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 5, color: "#666" }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: "#666" }, grid: { color: "#e3e3e3" } },
      },
    },
  });
}

function pushPoint(label, target, generated, saved, db) {
  series.labels.push(label);
  series.target.push(target);
  series.generated.push(generated);
  series.saved.push(saved);
  series.db.push(db);
  if (series.labels.length > 60) Object.values(series).forEach((list) => list.shift());
  if (chart) {
    chart.data.labels = series.labels;
    [series.target, series.generated, series.saved, series.db].forEach((data, i) => (chart.data.datasets[i].data = data));
    chart.update("none");
  }
}

const sumBy = (object, prefix) => Object.entries(object || {}).filter(([k]) => k.startsWith(prefix)).reduce((a, [, v]) => a + v, 0);
// Виміри кількості пригод у БД за останні 5 с: приріст рахується як середній
// за вікно, так само як швидкості лічильників у MetricsSink (підрозділ 4.8).
// Підрахунок кешується на 1 с, тож повторене значення — той самий вимір,
// а не зупинка запису: у вікно потрапляють лише нові виміри
const dbSamples = [];
const DB_WINDOW_SECONDS = 5;
let lastUpdate = 0;

function zoneLabels(count) {
  const lows = Array.from({ length: count }, (_, i) => Math.ceil((i * 1024) / count));
  return lows.map((low, i) => `«${String(low).padStart(4, "0")}»–«${String((lows[i + 1] ?? 1024) - 1).padStart(4, "0")}»`);
}

function onStats(s) {
  lastUpdate = Date.now();
  const c = s.counters || {};
  const r = s.rates || {};

  // керування швидкістю
  const instances = s.generator_instances || 0;
  if (!editingRate && document.activeElement !== $("rate-input")) showRate(s.target_rate);
  $("rate-actual").textContent = `${fmt(r["generator.generated"] || 0)} док/с`;
  const share = instances ? s.target_rate / instances : null;
  $("rate-note").textContent = `екземплярів генератора: ${instances || "—"} · частка кожного: ${share == null ? "—" : fmt(share) + " док/с"} · зміна діє з наступного пакета (≤ 1 с)`;

  // краулер
  $("cr-pages").textContent = fmt(c["crawler.pages_ok"] || 0);
  $("cr-rate").textContent = fmt1(r["crawler.pages_ok"] || 0);
  $("cr-errors").textContent = fmt(c["crawler.http_errors"] || 0);
  $("cr-throttled").textContent = fmt(c["crawler.throttled"] || 0);
  $("cr-robots").textContent = fmt(c["crawler.robots_skipped"] || 0);
  $("cr-cards").textContent = fmt(c["crawler.cards_parsed"] || 0);
  const stage = s.crawler_stage;
  $("cr-stage").textContent = `затримка між запитами 1,5–3 с · поточний етап: ${stage ? `${STAGE_LABELS[stage.stage] || stage.stage}, ${fmt(stage.done)} з ~${fmt(stage.total)}` : "—"}`;

  // показники
  $("k-crawled").textContent = fmt(c["crawler.pages_ok"] || 0);
  $("k-crawled-s").textContent = `${fmt1(r["crawler.pages_ok"] || 0)} стор/с`;
  $("k-csv").textContent = fmt(c["csv.rows"] || 0);
  $("k-csv-s").textContent = r["csv.rows"] ? `${fmt(r["csv.rows"])} рядків/с` : c["csv.rows"] ? "рядків · завершено" : "рядків";
  $("k-gen").textContent = fmt(c["generator.generated"] || 0);
  $("k-gen-s").textContent = `${fmt(r["generator.generated"] || 0)} док/с`;
  $("k-saved").textContent = fmt(sumBy(c, "ingest.saved."));
  $("k-saved-s").textContent = `${fmt(sumBy(r, "ingest.saved."))} док/с`;
  $("k-rejected").textContent = fmt(sumBy(c, "ingest.rejected."));
  $("k-queue").textContent = `${fmt(s.queue_depth)} / ${fmt(s.queue_limit)}`;
  const pressure = (r["queue.backpressure"] || 0) > 0 || s.queue_depth >= s.queue_limit;
  $("k-queue-s").textContent = pressure ? "зворотний тиск діє" : "зворотний тиск не діє";
  $("k-queue-s").className = pressure ? "s w" : "s m";

  // MongoDB: документи на шардах і приріст за даними самої БД
  const now = Date.now() / 1000;
  if (!dbSamples.length || s.adventures_total !== dbSamples[dbSamples.length - 1].total) {
    dbSamples.push({ total: s.adventures_total, time: now });
  }
  while (dbSamples.length > 2 && now - dbSamples[0].time > DB_WINDOW_SECONDS) dbSamples.shift();
  const first = dbSamples[0];
  const newest = dbSamples[dbSamples.length - 1];
  // нових вимірів понад 3 с — запис зупинився
  const dbRate = now - newest.time <= 3 && newest.time - first.time >= 2
    ? Math.max(0, (newest.total - first.total) / (newest.time - first.time)) : 0;
  const names = Object.keys(s.shards || {}).sort();
  const labels = zoneLabels(names.length);
  const max = Math.max(1, ...names.map((n) => s.shards[n]));
  $("shards").innerHTML = names.length
    ? names.map((n, i) => `<div class="shard"><div class="sname">${esc(n)}<small>${names.length > 1 ? labels[i] : "один вузол"}</small></div>
        <div class="sbar"><div style="width:${((s.shards[n] / max) * 100).toFixed(1)}%"></div></div><div class="sval">${fmt(s.shards[n])}</div></div>`).join("")
    : '<div class="muted small">немає даних</div>';
  $("db-total").textContent = fmt(s.adventures_total);
  $("db-rate").textContent = `${fmt(dbRate)} док/с`;
  const mean = s.adventures_total / Math.max(1, names.length);
  const deviation = names.length > 1 && mean > 0 ? Math.max(...names.map((n) => Math.abs(s.shards[n] - mean) / mean)) : null;
  $("db-dev").textContent = deviation == null ? "—" : `±${fmt1(deviation * 100)} %`;

  // графік навантаження
  const time = new Date(s.generated_at).toLocaleTimeString("uk-UA");
  pushPoint(time, s.target_rate, r["generator.generated"] || 0, r["ingest.saved.adventure"] || 0, dbRate);

  // статистика пошуку
  $("q-hit").textContent = pct(s.cache_hit_ratio);
  $("q-p50").textContent = s.latency_p50_ms == null ? "—" : `${fmt(s.latency_p50_ms)} мс`;
  $("q-p95").textContent = s.latency_p95_ms == null ? "—" : `${fmt(s.latency_p95_ms)} мс`;
  $("queries").innerHTML = (s.recent_queries || []).map((q) => `<tr>
      <td>${esc(new Date(q.created_at).toLocaleTimeString("uk-UA"))}</td><td>${esc(KIND_LABELS[q.kind] || q.kind)}</td>
      <td class="q">${esc(q.raw_query) || '<span class="muted">(без тексту)</span>'}</td>
      <td><span class="badge ${q.status === "FOUND" ? "ok" : "empty"}">${q.status === "FOUND" ? "Знайдено" : "Пусто"}</span></td>
      <td class="num">${fmt(q.results_count)}</td><td class="num">${fmt(q.latency_ms)} мс</td><td>${q.cache_hit ? "так" : "ні"}</td></tr>`).join("")
    || '<tr><td colspan="7" class="muted">запитів ще не було</td></tr>';

  // заповненість полів
  $("fill").innerHTML = Object.entries(s.fill || {}).map(([field, v]) =>
    `<tr><td>${esc(FILL_LABELS[field] || field)}</td><td class="num">${pct(v.collected)}</td><td class="num">${pct(v.generated)}</td></tr>`).join("");
}

function connectStream() {
  const live = $("live");
  const source = new EventSource("/api/stats/stream");
  source.onmessage = (event) => {
    live.classList.remove("off");
    onStats(JSON.parse(event.data));
  };
  source.addEventListener("error", (event) => {
    live.classList.add("off");
    live.querySelector("span").textContent = event.data ? `потік SSE: ${JSON.parse(event.data).detail}` : "потік SSE: перепідключення…";
  });
  setInterval(() => {
    if (!lastUpdate) return;
    const seconds = Math.round((Date.now() - lastUpdate) / 1000);
    if (!live.classList.contains("off")) live.querySelector("span").textContent = `потік SSE: оновлено ${seconds} с тому`;
  }, 1000);
}

// ---------------------------------------------------------------- пошук

const searchState = { page: 1, total: 0, kind: "adventures", items: [] };

function visibleFilters() {
  return document.getElementById(`filters-${$("kind").value}`);
}

function toggleFilters() {
  ["adventures", "monsters", "encounters"].forEach((k) => ($(`filters-${k}`).hidden = k !== $("kind").value));
}

function searchParams() {
  const params = new URLSearchParams();
  const q = $("q").value.trim();
  if (q) params.set("q", q);
  params.set("sort", $("sort").value);
  params.set("page", searchState.page);
  visibleFilters().querySelectorAll("input, select").forEach((el) => {
    const value = el.value.trim();
    if (!value) return;
    if (el.name === "tags") value.split(",").map((t) => t.trim()).filter(Boolean).forEach((t) => params.append("tags", t));
    else params.set(el.name, value);
  });
  return params;
}

function snippetHTML(snippet) {
  let out = "";
  let position = 0;
  for (const [start, end] of snippet.highlights || []) {
    out += esc(snippet.text.slice(position, start)) + "<mark>" + esc(snippet.text.slice(start, end)) + "</mark>";
    position = end;
  }
  return out + esc(snippet.text.slice(position));
}

const range = (a, b) => (a == null && b == null ? null : a === b || b == null ? `${a}` : `${a ?? "?"}–${b}`);
const settingName = (id) => (id && REF.settings[id] ? REF.settings[id].name : null);

function adventureFacts(a) {
  return [
    range(a.level_min, a.level_max) && `рівні ${range(a.level_min, a.level_max)}`,
    range(a.players_min, a.players_max) && `гравців ${range(a.players_min, a.players_max)}`,
    a.duration_hours != null && `${fmt1(a.duration_hours)} год`,
    a.system && `${a.system}${a.edition ? " " + a.edition : ""}`,
    settingName(a.setting_id),
    a.is_free ? "безкоштовна" : a.price != null ? `$${fmt1(a.price)}` : null,
    a.rating_avg != null && `★ ${fmt1(a.rating_avg)} (${fmt(a.ratings_count)})`,
    a.reviews_count && `відгуків ${fmt(a.reviews_count)}`,
    a.published_year,
    a.origin === "generated" ? "згенерована" : a.origin === "crawler" ? "з каталогу (краулер)" : a.origin === "csv" ? "з каталогу" : null,
  ].filter(Boolean).join(" · ");
}

function resultHTML(item, index) {
  const snippets = (item.snippets || []).slice(0, 3).map((s) => `<div class="snippet">${snippetHTML(s)}</div>`).join("");
  if (searchState.kind === "adventures") {
    const tags = item.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("");
    return `<div class="result" data-index="${index}"><h4>${esc(item.title)}</h4><div class="facts">${esc(adventureFacts(item))}</div>
      ${snippets || (item.synopsis ? `<div class="snippet">${esc(item.synopsis)}</div>` : "")}<div class="chips">${tags}</div></div>`;
  }
  if (searchState.kind === "monsters") {
    const facts = [item.type, `рівень небезпеки ${fmt1(item.challenge_rating)}`, item.environment.join(", "), `появ у реальних боях: ${fmt(item.play_count)}`].filter(Boolean).join(" · ");
    return `<div class="result" data-index="${index}"><h4>${esc(item.name)}</h4><div class="facts">${esc(facts)}</div>${snippets}</div>`;
  }
  const facts = [new Date(item.started_at).toLocaleString("uk-UA"), item.duration_minutes != null && `${fmt1(item.duration_minutes)} хв`,
    item.rounds_count != null && `раундів ${item.rounds_count}`, item.participants_count != null && `учасників ${item.participants_count}`,
    item.is_abandoned && "покинута сесія"].filter(Boolean).join(" · ");
  return `<div class="result" data-index="${index}"><h4>Зустріч ${esc(item.encounter_id)}</h4><div class="facts">${esc(facts)}</div>
    ${snippets || (item.narrative_excerpt ? `<div class="snippet">${esc(item.narrative_excerpt)}</div>` : "")}</div>`;
}

// Фільтр задано, якщо є будь-яке значення, крім тексту, сортування, сторінки
// й типового «виключити покинуті сесії» — так само вирішує сервер (is_empty)
const hasFilters = (params) => [...params.entries()].some(([k, v]) =>
  !["q", "sort", "page"].includes(k) && !(k === "exclude_abandoned" && v === "true"));

async function runSearch() {
  searchState.kind = $("kind").value;
  const params = searchParams();
  if (!params.get("q") && !hasFilters(params)) {
    // Порожній запит без фільтрів сервер не виконує (п. 4.6) — підказка замість «нічого не знайдено»
    $("results-summary").textContent = "";
    $("tag-counts").innerHTML = $("pager").innerHTML = "";
    $("results").innerHTML = '<div class="card muted">Введіть запит англійською, наприклад «cursed village» або «dragon lair», '
      + "чи задайте фільтр (рівень, кількість гравців, теги): запит без тексту й без фільтрів нічого не шукає.</div>";
    return;
  }
  $("results-summary").textContent = "пошук…";
  try {
    const result = await getJSON(`/api/search/${searchState.kind}?${params}`);
    searchState.total = result.total;
    searchState.items = result.items;
    $("results-summary").textContent = `знайдено ${fmt(result.total)} · ${fmt(result.latency_ms)} мс · кеш: ${result.cache_hit ? "так" : "ні"}`
      + (result.normalized_query ? ` · нормальна форма: «${result.normalized_query}»` : "");
    const activeTags = new Set(params.getAll("tags"));
    $("tag-counts").innerHTML = Object.entries(result.tag_counts || {})
      .map(([tag, count]) => `<span class="chip${activeTags.has(tag) ? " on" : ""}" data-tag="${esc(tag)}">${esc(tag)}<b>${fmt(count)}</b></span>`).join("");
    $("results").innerHTML = result.items.map(resultHTML).join("") || '<div class="card muted">Нічого не знайдено</div>';
    const pages = Math.max(1, Math.ceil(result.total / result.page_size));
    $("pager").innerHTML = result.total > result.page_size
      ? `<button class="btn light" data-page="${searchState.page - 1}" ${searchState.page <= 1 ? "disabled" : ""}>← попередня</button>
         <span class="muted">сторінка ${searchState.page} з ${fmt(pages)}</span>
         <button class="btn light" data-page="${searchState.page + 1}" ${searchState.page >= pages ? "disabled" : ""}>наступна →</button>`
      : "";
  } catch (error) {
    $("results-summary").innerHTML = `<span class="error">помилка пошуку: ${esc(error.message)}</span>`;
    $("results").innerHTML = "";
    $("pager").innerHTML = "";
  }
}

function initSearch() {
  $("kind").addEventListener("change", () => {
    toggleFilters();
    searchState.page = 1;
  });
  $("search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    searchState.page = 1;
    runSearch();
  });
  $("reset").addEventListener("click", () => {
    document.querySelectorAll(".filters input, .filters select").forEach((el) => (el.value = el.name === "exclude_abandoned" ? "true" : ""));
    $("q").value = "";
    $("tag-counts").innerHTML = $("results").innerHTML = $("pager").innerHTML = $("results-summary").textContent = "";
  });
  $("pager").addEventListener("click", (e) => {
    const page = e.target.dataset.page;
    if (page) {
      searchState.page = Number(page);
      runSearch();
    }
  });
  $("tag-counts").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const tags = new Set($("f-tags").value.split(",").map((t) => t.trim()).filter(Boolean));
    tags.has(chip.dataset.tag) ? tags.delete(chip.dataset.tag) : tags.add(chip.dataset.tag);
    $("f-tags").value = [...tags].join(", ");
    searchState.page = 1;
    runSearch();
  });
  $("results").addEventListener("click", (e) => {
    const element = e.target.closest(".result");
    if (!element) return;
    const item = searchState.items[Number(element.dataset.index)];
    if (searchState.kind === "adventures") openCard("adventure", item.adventure_id);
    else if (searchState.kind === "monsters") openCard("monster", item.monster_id);
    else showEncounter(item);
  });
}

// ---------------------------------------------------------------- картки

const link = (type, id, text) => `<a href="#" data-card="${type}" data-id="${esc(id)}">${esc(text ?? id)}</a>`;
const fact = (label, value) => (value == null || value === "" ? "" : `<div><span>${esc(label)}</span>${esc(value)}</div>`);

function openDrawer(html) {
  $("drawer-body").innerHTML = html;
  $("drawer").hidden = false;
  $("drawer").scrollTop = 0;
}

async function openCard(type, id) {
  openDrawer('<div class="muted">завантаження…</div>');
  const routes = { adventure: "adventures", monster: "monsters", author: "authors", character: "characters" };
  try {
    const card = await getJSON(`/api/${routes[type]}/${encodeURIComponent(id)}`);
    if (type === "adventure") return renderAdventure(card);
    if (type === "monster") return renderMonster(card);
    if (type === "author") return renderAuthor(card);
    return renderCharacter(card);
  } catch (error) {
    openDrawer(`<div class="error">картку не отримано: ${esc(error.message)}</div>`);
  }
}

async function fillNames(selector, route, nameOf) {
  const elements = [...document.querySelectorAll(selector)];
  await Promise.all(elements.map(async (el) => {
    try {
      const card = await getJSON(`/api/${route}/${encodeURIComponent(el.dataset.id)}`);
      el.textContent = nameOf(card);
    } catch {
      /* назва лишається ідентифікатором */
    }
  }));
}

function renderAdventure(a) {
  const reviews = a.reviews.map((r) => `<div class="review"><div class="who">${esc(r.author_name)}${r.posted_at ? " · " + esc(new Date(r.posted_at).toLocaleDateString("uk-UA")) : ""}</div>${esc(r.text)}</div>`).join("");
  openDrawer(`
    <h2>${esc(a.title)}</h2>
    <div class="muted small">${esc(adventureFacts(a))}</div>
    ${a.hook ? `<p><i>${esc(a.hook)}</i></p>` : ""}
    ${a.synopsis ? `<p>${esc(a.synopsis)}</p>` : ""}
    <div class="facts-grid">
      ${fact("система", [a.system, a.edition].filter(Boolean).join(" "))}${fact("ігровий світ", settingName(a.setting_id))}
      ${fact("рівні", range(a.level_min, a.level_max))}${fact("гравців", range(a.players_min, a.players_max))}
      ${fact("тривалість, год", a.duration_hours)}${fact("сесій", a.session_count)}${fact("складність", a.difficulty)}
      ${fact("рік", a.published_year)}${fact("сторінок", a.page_count)}${fact("видавництво", a.publisher)}
      ${fact("мова видання", a.publication_language)}${fact("жанр", a.genre)}${fact("статус", a.status)}
    </div>
    <h4>Автори</h4><div class="chips">${a.author_ids.map((id) => `<span class="chip">${link("author", id, "…")}</span>`).join("") || '<span class="muted small">—</span>'}</div>
    <h4>Монстри</h4><div class="chips">${a.monster_ids.map((id) => `<span class="chip">${link("monster", id, "…")}</span>`).join("") || '<span class="muted small">немає (картки каталогу переліку монстрів не містять)</span>'}</div>
    <h4>Теги</h4><div class="chips">${a.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("") || "—"}</div>
    ${a.required_books.length ? `<h4>Базові книги системи</h4><ul class="list">${a.required_books.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : ""}
    ${a.location_names.length ? `<h4>Локації</h4><div class="small">${esc(a.location_names.join(", "))}</div>` : ""}
    ${a.source_url ? `<h4>Сторінка модуля</h4><a href="${esc(a.source_url)}" target="_blank" rel="noopener">${esc(a.source_url)}</a>` : ""}
    ${a.similar_urls.length ? `<h4>Схожі модулі itch.io</h4>${a.similar_urls.map((u) => `<div><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a></div>`).join("")}` : ""}
    <h4>Схожі пригоди (граф: спільні теги й монстри)</h4><div id="similar" class="small muted">завантаження…</div>
    <h4>Ланцюжок кампанії (рівні 1–10, без повторів монстрів)</h4><div id="chain" class="small muted">завантаження…</div>
    ${reviews ? `<h4>Відгуки (${fmt(a.reviews_count)})</h4>${reviews}` : ""}`);
  fillNames('#drawer-body a[data-card="author"]', "authors", (c) => c.name);
  fillNames('#drawer-body a[data-card="monster"]', "monsters", (c) => c.name);
  getJSON(`/api/adventures/${encodeURIComponent(a.adventure_id)}/similar`)
    .then((items) => ($("similar").innerHTML = items.length
      ? `<ol class="list">${items.map((r) => `<li>${link("adventure", r.adventure_id, r.title)} <span class="muted">${esc(r.reason || "")}</span></li>`).join("")}</ol>`
      : "немає"))
    .catch((e) => ($("similar").textContent = `недоступно: ${e.message}`));
  getJSON(`/api/adventures/${encodeURIComponent(a.adventure_id)}/chain`)
    .then((items) => ($("chain").innerHTML = items.length > 1
      ? `<ol class="list">${items.map((r) => `<li>${link("adventure", r.adventure_id, r.title)} <span class="muted">рівні ${esc(range(r.level_min, r.level_max))}</span></li>`).join("")}</ol>`
      : "пригода не входить до ланцюжка кампанії"))
    .catch((e) => ($("chain").textContent = `недоступно: ${e.message}`));
}

function renderMonster(m) {
  const block = m.statblock || {};
  const abilities = Object.entries(block.abilities_scores || {}).map(([k, v]) => `${k.toUpperCase()} ${v}`).join(" · ");
  const speed = Object.entries(block.speed || {}).map(([k, v]) => `${k} ${v} ft.`).join(", ");
  const actions = [...(block.special_abilities || []), ...(block.actions || []), ...(block.legendary_actions || [])]
    .map((x) => `<li><b>${esc(x.name)}.</b> ${esc(x.description)}</li>`).join("");
  openDrawer(`
    <h2>${esc(m.name)}</h2>
    <div class="muted small">${esc([m.size, m.type, m.subtype && `(${m.subtype})`, m.alignment].filter(Boolean).join(" "))}</div>
    <div class="facts-grid" style="margin-top:10px">
      ${fact("рівень небезпеки", m.challenge_rating)}${fact("досвід", m.xp)}${fact("появ у реальних боях", m.play_count)}
      ${fact("середовище", m.environment.join(", "))}${fact("клас броні", block.armor_class)}${fact("хіти", block.hit_points)}
    </div>
    ${m.lore ? `<p>${esc(m.lore)}</p>` : ""}
    <div class="statblock">${speed ? `<div>Швидкість: ${esc(speed)}</div>` : ""}${abilities ? `<div>${esc(abilities)}</div>` : ""}</div>
    ${actions ? `<h4>Здібності й дії</h4><ul class="list">${actions}</ul>` : ""}
    <h4>Пригоди з цим монстром</h4>${m.appears_in.length ? `<ul class="list">${m.appears_in.map(([id, title]) => `<li>${link("adventure", id, title)}</li>`).join("")}</ul>` : '<div class="muted small">немає</div>'}
    <h4>Трапляються разом у реальних боях</h4>${m.seen_together.length ? `<ul class="list">${m.seen_together.map(([id, name, n]) => `<li>${link("monster", id, name)} <span class="muted">спільних боїв: ${fmt(n)}</span></li>`).join("")}</ul>` : '<div class="muted small">немає</div>'}`);
}

function renderAuthor(a) {
  openDrawer(`
    <h2>${esc(a.name)}</h2>
    ${a.profile_url ? `<a href="${esc(a.profile_url)}" target="_blank" rel="noopener">${esc(a.profile_url)}</a>` : ""}
    ${a.bio ? `<p>${esc(a.bio)}</p>` : ""}
    <div class="facts-grid">${fact("пригод", a.adventures_count)}${fact("середня оцінка", a.avg_rating)}${fact("перша публікація", a.first_published_year)}${fact("платформа", a.platform)}</div>
    <h4>Роботи</h4>${a.adventures.length ? `<ul class="list">${a.adventures.map((w) => `<li>${link("adventure", w.adventure_id, w.title)} <span class="muted">${esc(adventureFacts(w))}</span></li>`).join("")}</ul>` : '<div class="muted small">немає</div>'}
    <h4>Співавтори</h4>${a.co_authors.length ? `<div class="chips">${a.co_authors.map(([id, name]) => `<span class="chip">${link("author", id, name)}</span>`).join("")}</div>` : '<div class="muted small">немає</div>'}`);
}

function renderCharacter(c) {
  openDrawer(`
    <h2>${esc(c.name)}</h2>
    <div class="muted small">${esc([c.race, c.char_class, c.level && `рівень ${c.level}`].filter(Boolean).join(" · "))}</div>
    <div class="facts-grid" style="margin-top:10px">
      ${fact("класи", c.classes.map(([cl, lv]) => `${cl} ${lv}`).join(" / "))}${fact("мультиклас", c.is_multiclass ? "так" : "ні")}
      ${fact("хіти", c.hp_max != null ? `${c.hp_current ?? "?"}/${c.hp_max}` : null)}${fact("стан", c.hp_status)}${fact("зустрічей", c.encounters_count)}
    </div>
    <h4>Закляття</h4>${c.spells.length ? `<div class="chips">${c.spells.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div>` : '<div class="muted small">немає</div>'}
    ${c.attacks.length ? `<h4>Атаки</h4><div class="small">${esc(c.attacks.join(", "))}</div>` : ""}
    ${c.encounter_ids.length ? `<h4>Зустрічі</h4><div class="small">${esc(c.encounter_ids.slice(0, 30).join(", "))}</div>` : ""}`);
}

function showEncounter(e) {
  openDrawer(`
    <h2>Зустріч ${esc(e.encounter_id)}</h2>
    <div class="facts-grid">
      ${fact("початок", new Date(e.started_at).toLocaleString("uk-UA"))}${fact("тривалість, хв", e.duration_minutes)}
      ${fact("раундів", e.rounds_count)}${fact("учасників", e.participants_count)}${fact("покинута сесія", e.is_abandoned ? "так" : "ні")}
    </div>
    ${e.commands.length ? `<h4>Команди</h4><div class="chips">${e.commands.map((c) => `<span class="chip">${esc(c)}</span>`).join("")}</div>` : ""}
    <h4>Наратив ведучого</h4>${(e.snippets || []).map((s) => `<p>${snippetHTML(s)}</p>`).join("") || `<p>${esc(e.narrative_excerpt || "")}</p>`}
    <h4>Персонажі гравців</h4>${e.player_character_ids.length ? `<div class="chips">${e.player_character_ids.map((id) => `<span class="chip">${link("character", id, "…")}</span>`).join("")}</div>` : '<div class="muted small">немає</div>'}`);
  fillNames('#drawer-body a[data-card="character"]', "characters", (c) => c.name);
}

function initDrawer() {
  $("drawer-close").addEventListener("click", () => ($("drawer").hidden = true));
  document.addEventListener("keydown", (e) => e.key === "Escape" && ($("drawer").hidden = true));
  $("drawer").addEventListener("click", (e) => {
    const target = e.target.closest("a[data-card]");
    if (target) {
      e.preventDefault();
      openCard(target.dataset.card, target.dataset.id);
    }
  });
}

// ---------------------------------------------------------------- статистика гри

function barsHTML(rows) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return rows.map((r) => `<div class="row"><div>${r.label}</div><div class="bar"><div style="width:${((r.value / max) * 100).toFixed(1)}%"></div></div><div class="num">${fmt(r.value)}</div></div>`).join("")
    || '<div class="muted small">немає даних</div>';
}

async function loadStatistics() {
  getJSON("/api/statistics/monsters?limit=15")
    .then((s) => ($("st-monsters").innerHTML = barsHTML(s.popular_monsters.map(([id, name, n]) => ({ label: link("monster", id, name), value: n })))))
    .catch((e) => ($("st-monsters").innerHTML = `<div class="error">${esc(e.message)}</div>`));
  getJSON("/api/statistics/combat")
    .then((s) => ($("st-combat").innerHTML = `<div>медіана тривалості<b>${s.median_duration_minutes == null ? "—" : fmt1(s.median_duration_minutes) + " хв"}</b></div>
      <div>медіана раундів<b>${s.median_rounds == null ? "—" : fmt1(s.median_rounds)}</b></div>`))
    .catch((e) => ($("st-combat").innerHTML = `<div class="error">${esc(e.message)}</div>`));
  getJSON("/api/statistics/party?limit=10")
    .then((s) => {
      $("st-party").innerHTML = barsHTML(s.party_composition.map(([cls, n]) => ({ label: esc(cls ?? "—"), value: n })));
      $("st-classrace").innerHTML = s.class_race.map(([cls, race, n]) => `<tr><td>${esc(cls ?? "—")}</td><td>${esc(race ?? "—")}</td><td class="num">${fmt(n)}</td></tr>`).join("")
        || '<tr><td colspan="3" class="muted">немає даних</td></tr>';
    })
    .catch((e) => ($("st-party").innerHTML = `<div class="error">${esc(e.message)}</div>`));
  loadSpells();
}

async function loadSpells() {
  const params = new URLSearchParams({ char_class: $("st-class").value, level: $("st-level").value || "5", limit: "12" });
  try {
    const s = await getJSON(`/api/statistics/spells?${params}`);
    $("st-spells").innerHTML = barsHTML(s.typical_spells.map(([spell, n]) => ({ label: esc(spell), value: n })));
  } catch (e) {
    $("st-spells").innerHTML = `<div class="error">${esc(e.message)}</div>`;
  }
}

$("st-spells-go").addEventListener("click", loadSpells);
document.getElementById("tab-stats").addEventListener("click", (e) => {
  const target = e.target.closest("a[data-card]");
  if (target) {
    e.preventDefault();
    openCard(target.dataset.card, target.dataset.id);
  }
});

// ---------------------------------------------------------------- старт

loadReference();
initRateControl();
initChart();
initSearch();
initDrawer();
toggleFilters();
connectStream();
