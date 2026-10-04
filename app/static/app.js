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
    const data = await resp.json();
    if (seq !== reverseSeq) return; // a newer click superseded this one
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
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

// Live countdown while the backend pauses before retrying failed mirrors.
let waitTimer = null;
function showWaitCountdown(seconds) {
  clearInterval(waitTimer);
  let remaining = seconds;
  const paint = () =>
    setStatusLoading(
      `All map data servers are busy — retrying in ${remaining}s…`
    );
  paint();
  waitTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(waitTimer);
      waitTimer = null;
      setStatusLoading("Retrying map data servers…");
      return;
    }
    paint();
  }, 1000);
}
function clearWaitCountdown() {
  clearInterval(waitTimer);
  waitTimer = null;
}

function scoreColor(score) {
  // 0 -> red, 100 -> green
  const hue = Math.round(score * 1.2);
  return `hsl(${hue}, 70%, 42%)`;
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
const TOP_N = 12;

// Coarse 3-stop sliders: one notch each way from neutral.
const SLIDER_STOPS = {
  0: { w: 0.5, label: "Less" },
  1: { w: 1, label: "Same" },
  2: { w: 2, label: "More" },
};

function buildSliders() {
  const host = document.getElementById("weight-sliders");
  host.innerHTML = "";
  const cats = Object.keys(categoryLabels).length
    ? Object.keys(categoryLabels)
    : Object.keys(ICONS);
  for (const cat of cats) {
    const row = document.createElement("div");
    row.className = "wrow";
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
  for (const m of ranked) {
    const mk = markersByName.get(m.name);
    if (mk) {
      mk.setStyle({ fillColor: scoreColor(m.score) });
      mk.setPopupContent(popupHtml(m));
    }
  }
  drawCards(ranked.slice(0, TOP_N));
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

function popupHtml(m) {
  const rows = Object.keys(m.profile)
    .filter((k) => m.profile[k] > 0)
    .map(
      (k) =>
        `<div>${icon(k)}<strong>${escapeHtml(categoryLabels[k] || k)}:</strong> ${m.profile[k]}</div>`
    )
    .join("");
  return `<b>${escapeHtml(m.name)}</b><br>${m.score}% match · ${m.distance_km} km away<div style="margin-top:6px">${rows || "No POI data"}</div>`;
}

function cardHtml(m, srcProfile, maxCount) {
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
          <span class="counts">${s} → ${c}</span>
        </div>`;
    })
    .join("");
  return `
    <div class="card" data-name="${escapeHtml(m.name)}">
      <div class="card-head">
        <h3>${escapeHtml(m.name)}</h3>
        <span class="score-badge" style="background:${scoreColor(m.score)}">${m.score}%</span>
      </div>
      <div class="meta">${escapeHtml(m.place_type)} · ${m.distance_km} km away</div>
      <div class="cmp">${rows}</div>
    </div>`;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const address = addressInput.value.trim();
  if (!address) return;

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
    const msg = err.name === "AbortError" ? "Request timed out." : err.message;
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
  drawCards(ranked.slice(0, TOP_N));
}

// Map layers are drawn once per search — all evaluated candidates get
// markers, coloured by their current score.
function drawMap(ranked) {
  layerGroup.clearLayers();
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

  for (const m of ranked) {
    const marker = L.circleMarker([m.lat, m.lon], {
      radius: 9,
      color: "#fff",
      weight: 2,
      fillColor: scoreColor(m.score),
      fillOpacity: 0.95,
    }).addTo(layerGroup);
    marker.bindPopup(popupHtml(m));
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
    `<div class="legend">
       ${data.candidates_evaluated} of ${data.candidates_found} neighbourhoods evaluated ·
       top ${top.length} shown${data.partial ? " · partial (time limit)" : ""} ·
       <span class="swatch" style="background:#94a3b8"></span>yours
       <span class="swatch" style="background:#2563eb"></span>match
     </div>` +
    top.map((m) => cardHtml(m, data.source.profile, maxCount)).join("");

  resultsEl.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("click", () => {
      const marker = markersByName.get(card.dataset.name);
      if (marker) {
        map.setView(marker.getLatLng(), Math.max(map.getZoom(), 13));
        marker.openPopup();
      }
    });
  });
}
