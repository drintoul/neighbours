const map = L.map("map").setView([49.1044, -122.6604], 11); // Langley, BC
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

let categoryLabels = {};
let layerGroup = L.layerGroup().addTo(map);
const markersByName = new Map();

// Leaflet caches container size — recalc on rotate/resize to avoid grey tiles
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => map.invalidateSize(), 150);
});

fetch("/api/categories")
  .then((r) => r.json())
  .then((d) => {
    categoryLabels = d.categories;
    if (!slidersBuilt) buildSliders();
    if (d.data_as_of) {
      const el = document.getElementById("data-asof");
      const date = new Date(d.data_as_of);
      if (el && !isNaN(date)) {
        el.textContent = `, updated ${date.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}`;
      }
    }
  })
  .catch(() => {});

const form = document.getElementById("search-form");
const addressInput = document.getElementById("address");
const radiusInput = document.getElementById("radius");
const radiusValue = document.getElementById("radius-value");
const searchBtn = document.getElementById("search-btn");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");

radiusInput.addEventListener("input", () => {
  radiusValue.textContent = radiusInput.value;
});

// Map click → reverse-geocode the point into the search bar
let clickMarker = null;
let reverseSeq = 0;

map.on("click", async (e) => {
  // Ignore clicks that landed on a marker/popup (SVG paths), not the map itself
  if (e.originalEvent.target instanceof SVGElement) return;

  const { lat, lng } = e.latlng;
  const seq = ++reverseSeq;

  if (clickMarker) layerGroup.removeLayer(clickMarker);
  clickMarker = L.circleMarker([lat, lng], {
    radius: 7,
    color: "#16a34a",
    weight: 2,
    fillColor: "#16a34a",
    fillOpacity: 0.55,
  }).addTo(layerGroup);
  clickMarker.bindTooltip("Looking up address…").openTooltip();

  try {
    const resp = await fetch(`/api/reverse?lat=${lat}&lon=${lng}`);
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    if (seq !== reverseSeq) return; // a newer click superseded this one
    addressInput.value = data.display_name;
    clickMarker.bindTooltip(data.short_name).openTooltip();
    setStatus(`Using "${data.short_name}" — adjust the radius and hit Find similar.`);
    setTimeout(() => {
      if (seq === reverseSeq) clearStatus();
    }, 6000);
  } catch (err) {
    if (seq !== reverseSeq) return;
    clickMarker.bindTooltip("No address found here").openTooltip();
    setStatus(err.message || "Couldn't find an address at that location.", true);
  }
});

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

function clearStatus() {
  // :empty keeps the bar invisible but preserves its space (no layout jump)
  statusEl.textContent = "";
  statusEl.classList.remove("error");
}

function setStatusLoading(msg) {
  statusEl.textContent = "";
  const sp = document.createElement("span");
  sp.className = "spinner";
  const tx = document.createElement("span");
  tx.textContent = " " + msg;
  statusEl.append(sp, tx);
  statusEl.classList.remove("error");
}

// Live countdown — used for mirror-wait retries and 429 auto-retries.
let waitTimer = null;
function showWaitCountdown(
  seconds,
  prefix = "All map data servers are busy — retrying in",
  doneMsg = "Retrying map data servers…"
) {
  clearInterval(waitTimer);
  let remaining = seconds;
  const paint = () => setStatusLoading(`${prefix} ${remaining}s…`);
  paint();
  waitTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(waitTimer);
      waitTimer = null;
      setStatusLoading(doneMsg);
      return;
    }
    paint();
  }, 1000);
}
function clearWaitCountdown() {
  clearInterval(waitTimer);
  waitTimer = null;
}

function rankColor(i, n) {
  // Colour by rank within this result set — absolute score thresholds leave
  // every dot green when matches cluster at 90%+. Fractions of the ranked
  // list always spread: ~top 15% green, next ~35% yellow/gold, next ~30%
  // orange, tail grey.
  const t = n <= 1 ? 0 : i / n;
  if (t < 0.15) return "#16a34a"; // green — top tier
  if (t < 0.5) return "#ca8a04"; // yellow/gold — 2nd tier
  if (t < 0.8) return "#ea580c"; // orange — 3rd tier
  return "#64748b"; // slate grey — weakest candidates
}

// Minimal stroke icons per category (24x24, Lucide-style)
const ICONS = {
  restaurants: "M5 3v6a2 2 0 0 0 4 0V3M7 3v4M7 11v10M17 3c-1.7 0-3 2.5-3 6 0 2 1 3 3 3v9M17 3v6",
  cafes: "M17 9h1.5a3.5 3.5 0 0 1 0 7H17M3 9h14v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9zM7 3v3M11 3v3M15 3v3",
  nightlife: "M5 4h14l-7 8zM12 12v9M8 21h8",
  shopping: "M5 8h14l-1.2 13H6.2zM9 8a3 3 0 0 1 6 0",
  groceries: "M2 4h3l2.6 11.5h11.2L22 8H6.5M9 20.5h.01M18 20.5h.01",
  parks: "M12 3l5 6h-3l4 6H6l4-6H7zM12 15v7",
  waterfront: "M2 7c2-2 4-2 6 0s4 2 6 0 4-2 6 0M2 12c2-2 4-2 6 0s4 2 6 0 4-2 6 0M2 17c2-2 4-2 6 0s4 2 6 0 4-2 6 0",
  culture: "M3 10l9-6 9 6H3zM5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 18h18",
  fitness: "m6.5 6.5 11 11M21 21l-1-1M3 3l1 1M18 22l4-4M2 6l4-4M3 10l7-7M14 21l7-7",
  schools: "M2 9l10-5 10 5-10 5zM6 11.5V16c0 1.7 2.7 3 6 3s6-1.3 6-3v-4.5M22 9v5",
  transit: "M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM4 11h16M8 15h.01M16 15h.01M7 20v-3M17 20v-3",
  lodging: "M3 20V6M3 14h18v6M3 20h18M8 14v-3h6v3",
  healthcare: "M9 3h6v6h6v6h-6v6H9v-6H3V9h6z",
  cycling: "M5.5 21a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm13 0a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM5.5 17.5 9 9h5l4.5 8.5M9 9l2-4h3",
  nuisance: "M4 21V9.5L10 14V9.5L16 14V5h4v16zM2 21h20",
};

function icon(name) {
  const d = ICONS[name];
  if (!d) return "";
  return `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
}

// ---- Feature weight sliders ----
let lastData = null;
let slidersBuilt = false;
let rankTimer = null;

// How many cards to show — follows the backend's configured TOP_MATCHES
// (length of the `matches` array), defaulting to 12.
function topN() {
  return (lastData && lastData.matches && lastData.matches.length) || 12;
}

// Coarse 3-stop sliders: one notch each way from neutral.
const SLIDER_STOPS = {
  0: { w: 0.5, label: "Less" },
  1: { w: 1, label: "Same" },
  2: { w: 2, label: "More" },
};

// Hover tooltips: what each category counts + what the slider does.
const CAT_TOOLTIPS = {
  restaurants: "Restaurants, fast food, food courts, food halls.",
  cafes: "Cafés, coffee shops, ice cream parlours.",
  nightlife: "Bars, pubs, nightclubs, biergartens.",
  shopping: "Shops of every kind except grocery-type stores and bike shops.",
  groceries: "Supermarkets, bakeries, greengrocers, delis, markets.",
  lodging: "Hotels, hostels, guest houses, motels — touristy vs residential.",
  healthcare: "Hospitals, clinics, pharmacies, dentists, doctors.",
  schools: "Schools, kindergartens, universities, childcare.",
  parks: "Parks, gardens, playgrounds, woods, meadows, green space.",
  waterfront: "Beaches, coastline, bays, rivers, lakes, marinas.",
  culture: "Cinemas, theatres, museums, galleries, libraries, artwork.",
  fitness: "Gyms, pools, pitches, courts, golf courses, sports centres.",
  transit: "Bus stops, train/tram/subway stations, ferry terminals.",
  cycling: "Cycleways, bike parking, rentals, repair shops, bike stores.",
  nuisance:
    "Negative feature — less of it is better. 'More' penalises areas " +
    "whose industrial footprint differs from yours. Counts industrial " +
    "land, rail lines, aerodromes, power plants, landfills, works.",
};

function buildSliders() {
  const host = document.getElementById("weight-sliders");
  host.innerHTML = "";
  const cats = Object.keys(categoryLabels).length
    ? Object.keys(categoryLabels)
    : Object.keys(ICONS);
  for (const cat of cats) {
    const row = document.createElement("div");
    row.className = "wrow" + (cat === "nuisance" ? " negative" : "");
    const tip =
      (CAT_TOOLTIPS[cat] ? CAT_TOOLTIPS[cat] + " " : "") +
      "'More' weighs this category heavier when ranking matches; 'Less' weighs it lighter.";
    row.title = tip;
    row.innerHTML =
      `<div class="wlabel">${icon(cat)}<span>${escapeHtml(categoryLabels[cat] || cat)}</span><span class="wval">Same · 1×</span></div>` +
      `<input type="range" class="wslider" data-cat="${cat}" min="0" max="2" step="1" value="1" list="weight-ticks">`;
    host.appendChild(row);
  }
  host.querySelectorAll(".wslider").forEach((s) =>
    s.addEventListener("input", onSliderInput)
  );
  slidersBuilt = true;
}

function updateSliderVal(slider) {
  const stop = SLIDER_STOPS[slider.value];
  slider.closest(".wrow").querySelector(".wval").textContent =
    `${stop.label} · ${stop.w}×`;
}

function currentWeights() {
  const w = {};
  document.querySelectorAll(".wslider").forEach((s) => {
    w[s.dataset.cat] = SLIDER_STOPS[s.value].w;
  });
  return w;
}

// Same scoring as the backend: cosine similarity of log-scaled,
// weight-scaled category vectors.
function weightedScore(src, cand, w) {
  let dot = 0, na = 0, nb = 0;
  for (const c of Object.keys(src)) {
    const a = Math.log1p(src[c] || 0) * (w[c] ?? 1);
    const b = Math.log1p(cand[c] || 0) * (w[c] ?? 1);
    dot += a * b;
    na += a * a;
    nb += b * b;
  }
  return na && nb ? dot / Math.sqrt(na * nb) : 0;
}

function onSliderInput(e) {
  updateSliderVal(e.target);
  clearTimeout(rankTimer);
  rankTimer = setTimeout(applyRank, 150);
}

// Re-rank locally — profiles are already here, so no API call needed.
function rankPool() {
  const w = currentWeights();
  const pool =
    lastData.evaluated && lastData.evaluated.length
      ? lastData.evaluated
      : lastData.matches;
  return pool
    .map((m) => ({
      ...m,
      score: Math.round(weightedScore(lastData.source.profile, m.profile, w) * 100),
    }))
    .sort((a, b) => b.score - a.score);
}

function applyRank() {
  if (!lastData) return;
  const ranked = rankPool();
  // Update existing markers in place — no layer rebuild, no fitBounds,
  // so the map view and any open popup stay put while dragging.
  for (const [i, m] of ranked.entries()) {
    const mk = markersByName.get(m.name);
    if (mk) {
      mk.setStyle({ fillColor: rankColor(i, ranked.length) });
      mk.setPopupContent(popupHtml(m, lastData.source.profile));
    }
  }
  drawCards(ranked.slice(0, topN()));
}

document.getElementById("weights-reset").addEventListener("click", () => {
  document.querySelectorAll(".wslider").forEach((s) => {
    s.value = 1;
    updateSliderVal(s);
  });
  applyRank();
});

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function popupHtml(m, srcProfile) {
  const rows = Object.keys(m.profile)
    .filter((k) => m.profile[k] > 0 || (srcProfile && srcProfile[k] > 0))
    .map(
      (k) =>
        `<div>${icon(k)}<strong>${escapeHtml(categoryLabels[k] || k)}:</strong> ` +
        `${srcProfile ? deltaHtml(k, srcProfile[k] || 0, m.profile[k]) : m.profile[k]}</div>`
    )
    .join("");
  const drive = m.drive_s
    ? ` · ~${Math.max(1, Math.round(m.drive_s / 60))} min drive`
    : "";
  return `<b>${escapeHtml(m.name)}</b><br>${m.score}% match · ${m.distance_km} km away${drive}<div style="margin-top:6px">${rows || "No POI data"}</div>`;
}

function deltaHtml(cat, s, c) {
  const d = c - s;
  // For nuisances, "more" is the bad direction — flip the colour.
  const cls = d === 0 ? "same" : (cat === "nuisance" ? d < 0 : d > 0) ? "good" : "bad";
  let txt;
  if (d === 0) txt = "±0";
  // No baseline to divide by — report the raw count instead of a percentage.
  else if (s === 0) txt = `+${d} new`;
  else txt = `${d > 0 ? "+" : "−"}${Math.abs(Math.round((d / s) * 100))}%`;
  return `<span class="counts diff ${cls}" title="vs your area">${txt}</span>`;
}

function cardHtml(m, srcProfile, maxCount, rank, total) {
  const cats = Object.keys(categoryLabels).length
    ? Object.keys(categoryLabels)
    : Object.keys(srcProfile);
  const rows = cats
    .filter((k) => (srcProfile[k] || 0) > 0 || (m.profile[k] || 0) > 0)
    .map((k) => {
      const s = srcProfile[k] || 0;
      const c = m.profile[k] || 0;
      const scale = Math.max(maxCount, 1);
      return `<div class="label">${icon(k)}${escapeHtml(categoryLabels[k] || k)}</div>
        <div class="bars">
          <span class="bar src" style="width:${Math.round((s / scale) * 60)}px"></span>
          <span class="bar cand" style="width:${Math.round((c / scale) * 60)}px"></span>
          ${deltaHtml(k, s, c)}
        </div>`;
    })
    .join("");
  return `
    <div class="card" data-name="${escapeHtml(m.name)}">
      <div class="card-head">
        <h3>${escapeHtml(m.name)}</h3>
        <span class="score-badge" style="background:${rankColor(rank, total)}">${m.score}%</span>
      </div>
      <div class="meta">${escapeHtml(m.place_type)} · ${m.distance_km} km away${
        m.drive_s
          ? ` · ~${Math.max(1, Math.round(m.drive_s / 60))} min drive (${m.drive_km} km)`
          : ""
      }</div>
      <div class="cmp">${rows}</div>
    </div>`;
}

// Pinned reference card for the submitted address — every card below is
// compared against this profile, so it sits at the top of the list.
function srcCardHtml(src, maxCount) {
  const cats = Object.keys(categoryLabels).length
    ? Object.keys(categoryLabels)
    : Object.keys(src.profile);
  const rows = cats
    .filter((k) => (src.profile[k] || 0) > 0)
    .map((k) => {
      const s = src.profile[k] || 0;
      const scale = Math.max(maxCount, 1);
      return `<div class="label">${icon(k)}${escapeHtml(categoryLabels[k] || k)}</div>
        <div class="bars">
          <span class="bar src" style="width:${Math.round((s / scale) * 60)}px"></span>
          <span class="counts">${s}</span>
        </div>`;
    })
    .join("");
  return `
    <div class="card src-card">
      <div class="card-head">
        <h3>${escapeHtml(src.name)}</h3>
        <span class="score-badge src-badge">your area</span>
      </div>
      <div class="meta">${escapeHtml(src.display_name)}</div>
      <div class="cmp">${rows}</div>
    </div>`;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const address = addressInput.value.trim();
  if (!address) return;

  // Reflect the search in the URL so results can be shared/bookmarked.
  history.replaceState(
    null,
    "",
    "?" +
      new URLSearchParams({ address, radius: radiusInput.value }).toString()
  );

  searchBtn.disabled = true;
  searchBtn.classList.add("loading");
  setStatusLoading("Starting search…");
  resultsEl.innerHTML =
    '<div class="placeholder"><span class="spinner big"></span>Gathering map data…</div>';

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 300000);

  try {
    const resp = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address, radius_km: Number(radiusInput.value) }),
      signal: controller.signal,
    });
    if (!resp.ok || !resp.body) {
      const err = await resp.json().catch(() => ({}));
      if (resp.status === 429) {
        // Server says exactly when the rolling window frees up — count down
        // and resubmit automatically. Re-entrant resubmit happens after this
        // frame's finally runs, keeping button/timer state clean.
        const waitS =
          Math.min(parseInt(resp.headers.get("retry-after") || "65", 10) + 1, 130);
        showWaitCountdown(
          waitS,
          "Rate limit — one search per minute. Retrying in",
          "Retrying search…"
        );
        await new Promise((r) => setTimeout(r, waitS * 1000));
        setTimeout(() => form.requestSubmit(), 0);
        return;
      }
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    // Server streams newline-delimited JSON: progress events, then a
    // final "result" or "error" object.
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalData = null;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 1);
        if (!line) continue;
        const msg = JSON.parse(line);
        if (msg.type === "progress") {
          clearWaitCountdown();
          setStatusLoading(msg.message);
        } else if (msg.type === "wait") {
          showWaitCountdown(msg.seconds);
        } else if (msg.type === "result") finalData = msg.data;
        else if (msg.type === "error") throw new Error(msg.detail || "Search failed");
      }
    }
    if (!finalData) throw new Error("No result received.");
    render(finalData);
    clearStatus();
  } catch (err) {
    const msg =
      err.name === "AbortError"
        ? "Request timed out."
        : /stream|network|terminated|aborted/i.test(err.message || "")
        ? "Connection to the server was interrupted — please try again."
        : err.message;
    setStatus(msg, true);
    resultsEl.innerHTML = '<div class="placeholder">Search failed — see message above.</div>';
  } finally {
    clearTimeout(timer);
    clearWaitCountdown();
    searchBtn.classList.remove("loading");
    searchBtn.disabled = false;
  }
});

function render(data) {
  lastData = data;
  if (!slidersBuilt) buildSliders();
  const ranked = rankPool();
  drawMap(ranked);
  drawCards(ranked.slice(0, topN()));
}

// Driving route preview for the clicked match — drawn over everything else.
let routeLayer = null;

async function showRoute(m) {
  const src = lastData && lastData.source;
  if (!src) return;
  try {
    const r = await fetch(
      `/api/route?from_lat=${src.lat}&from_lon=${src.lon}` +
        `&to_lat=${m.lat}&to_lon=${m.lon}`
    );
    if (!r.ok) return;
    const d = await r.json();
    if (routeLayer) layerGroup.removeLayer(routeLayer);
    routeLayer = L.geoJSON(d.geometry, {
      style: { color: "#7c3aed", weight: 3, opacity: 0.75 },
    }).addTo(layerGroup);
    map.fitBounds(routeLayer.getBounds().pad(0.12));
  } catch {}
}

// Map layers are drawn once per search — all evaluated candidates get
// markers, coloured by their current score.
function drawMap(ranked) {
  layerGroup.clearLayers();
  routeLayer = null;
  markersByName.clear();

  const data = lastData;
  const src = data.source;
  const bounds = [];

  // Search-radius circle (dashed)
  L.circle([src.lat, src.lon], {
    radius: data.search_radius_km * 1000,
    color: "#94a3b8",
    weight: 1,
    dashArray: "6 6",
    fill: false,
  }).addTo(layerGroup);

  // Source marker + its profile neighbourhood radius
  L.circle([src.lat, src.lon], {
    radius: data.profile_radius_m,
    color: "#dc2626",
    weight: 1.5,
    fillColor: "#dc2626",
    fillOpacity: 0.05,
  }).addTo(layerGroup);

  const srcMarker = L.marker([src.lat, src.lon]).addTo(layerGroup);
  srcMarker.bindPopup(`<b>${escapeHtml(src.name)}</b><br>${escapeHtml(src.display_name)}`);
  bounds.push([src.lat, src.lon]);

  for (const [i, m] of ranked.entries()) {
    const marker = L.circleMarker([m.lat, m.lon], {
      radius: 9,
      color: "#fff",
      weight: 2,
      fillColor: rankColor(i, ranked.length),
      fillOpacity: 0.95,
    }).addTo(layerGroup);
    marker.bindPopup(popupHtml(m, src.profile));
    markersByName.set(m.name, marker);
    bounds.push([m.lat, m.lon]);
  }

  if (bounds.length) map.fitBounds(L.latLngBounds(bounds).pad(0.15));
}

// Results panel cards — rebuilt on every rank change.
function drawCards(top) {
  const data = lastData;
  if (!top.length) {
    resultsEl.innerHTML = '<div class="placeholder">No named neighbourhoods found in this radius.</div>';
    return;
  }
  const maxCount = Math.max(
    1,
    ...top.flatMap((m) => Object.values(m.profile)),
    ...Object.values(data.source.profile)
  );
  resultsEl.innerHTML =
    srcCardHtml(data.source, maxCount) +
    `<div class="legend">
       ${data.candidates_evaluated} of ${data.candidates_found} neighbourhoods evaluated ·
       top ${top.length} shown${data.partial ? " · partial (time limit)" : ""} ·
       <span class="swatch" style="background:#94a3b8"></span>yours
       <span class="swatch" style="background:#2563eb"></span>match
     </div>` +
    top
      .map((m, i) =>
        cardHtml(
          m,
          data.source.profile,
          maxCount,
          i,
          (data.evaluated && data.evaluated.length) || top.length
        )
      )
      .join("");

  const srcCard = resultsEl.querySelector(".src-card");
  if (srcCard)
    srcCard.addEventListener("click", () =>
      map.setView([data.source.lat, data.source.lon], Math.max(map.getZoom(), 13))
    );

  const byName = new Map(top.map((m) => [m.name, m]));
  resultsEl.querySelectorAll(".card:not(.src-card)").forEach((card) => {
    card.addEventListener("click", () => {
      const marker = markersByName.get(card.dataset.name);
      if (marker) {
        map.setView(marker.getLatLng(), Math.max(map.getZoom(), 13));
        marker.openPopup();
      }
      const m = byName.get(card.dataset.name);
      if (m) showRoute(m);
    });
  });
}

// Shareable search URLs: ?address=…&radius=… pre-fills and auto-runs.
// The auto-run is throttled per tab — otherwise every page reload burns the
// per-IP search quota and the rate limit never appears to clear.
const urlParams = new URLSearchParams(location.search);
const urlAddress = urlParams.get("address");
if (urlAddress) {
  addressInput.value = urlAddress;
  const urlRadius = parseFloat(urlParams.get("radius"));
  if (urlRadius >= 1 && urlRadius <= 50) {
    radiusInput.value = urlRadius;
    radiusValue.textContent = urlRadius;
  }
  const lastAutoRun = Number(sessionStorage.getItem("lastAutoRunAt") || 0);
  if (Date.now() - lastAutoRun > 60000) {
    sessionStorage.setItem("lastAutoRunAt", Date.now());
    form.requestSubmit();
  } else {
    setStatus("Search already ran recently — press Find similar to run it again.");
  }
}
