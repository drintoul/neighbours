const form = document.getElementById("search-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const hotelCard = document.getElementById("hotel-card");
const groupsEl = document.getElementById("groups");

let map = null;
let layer = null;
let spotMarkers = [];
let selected = null;

// Bigger tap targets on touch devices
const COARSE = matchMedia("(pointer: coarse)").matches;
const DOT_STYLE = {
  radius: COARSE ? 8 : 6,
  color: "#fff",
  weight: 1.5,
  fillColor: "#e8590c",
  fillOpacity: 0.9,
};
const DOT_SELECTED = { fillColor: "#c92a2a", radius: COARSE ? 11 : 9 };

// Per-cuisine avatar colors, stable across renders
const AV_COLORS = [
  "#e8590c", "#0ca678", "#1971c2", "#9c36b5", "#d6336c",
  "#e67700", "#1098ad", "#6741d9", "#2f9e44", "#c2255c",
];
function avColor(name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return AV_COLORS[h % AV_COLORS.length];
}

// Icons keyed by the backend's broad cuisine groups
const CUISINE_ICONS = {
  "pizza & italian": "🍕",
  "chinese": "🥡",
  "japanese & sushi": "🍣",
  "asian": "🍜",
  "indian & middle eastern": "🍛",
  "burgers & american": "🍔",
  "chicken & wings": "🍗",
  "mexican & latin": "🌮",
  "mediterranean": "🥙",
  "sandwiches & deli": "🥪",
  "cafés & coffee": "☕",
  "breakfast & brunch": "🍳",
  "dessert & bakery": "🧁",
  "european": "🥐",
  "fast food": "🍟",
  "seafood": "🐟",
  "healthy & salad": "🥗",
  "pubs & bars": "🍺",
  "other": "🍽️",
};
function cuisineIcon(group) {
  return CUISINE_ICONS[group.toLowerCase()] || "🍽️";
}

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

function mins(s) {
  return `${Math.max(1, Math.round(s / 60))} min`;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function mapsUrl(s) {
  // Name + address lands on the actual place (not a raw-coords pin);
  // coords are the fallback when no address text exists.
  const q = s.address ? `${s.name}, ${s.address}` : `${s.lat},${s.lon}`;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}`;
}

function telHref(phone) {
  return `tel:${phone.replace(/[^+\d]/g, "")}`;
}

// "Open now"/"Closed" verdict — Google's openNow when enriched, else the
// OSM opening_hours tag evaluated locally. No data → no badge.
function openBadge(s) {
  let open, next;
  if (s.open_now != null) {
    open = s.open_now;
  } else if (s.opening_hours && typeof opening_hours === "function") {
    try {
      const oh = new opening_hours(s.opening_hours);
      open = oh.getState();
      next = oh.getNextChange();
    } catch {
      return "";
    }
  } else {
    return "";
  }
  let label = open ? "Open now" : "Closed";
  if (next) {
    const sameDay = next.toDateString() === new Date().toDateString();
    const t = next.toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
      ...(sameDay ? {} : { weekday: "short" }),
    });
    label += open ? ` · closes ${t}` : ` · opens ${t}`;
  }
  return `<span class="open ${open ? "yes" : "no"}">${esc(label)}</span>`;
}

// Google rating / price / phone / website line — only fields that exist.
function detailsLine(s) {
  const bits = [];
  if (s.rating != null)
    bits.push(
      `<a class="rating" href="${esc(s.google_maps_uri || mapsUrl(s))}" ` +
        `target="_blank" rel="noopener">★ ${s.rating} (${s.review_count || 0})</a>`
    );
  if (s.price) bits.push(`<span class="price">${esc(s.price)}</span>`);
  if (s.phone)
    bits.push(`<a href="${telHref(s.phone)}">📞 ${esc(s.phone)}</a>`);
  if (s.website)
    bits.push(
      `<a href="${esc(s.website)}" target="_blank" rel="noopener">🌐 website</a>`
    );
  if (!bits.length) return "";
  return `<div class="meta details">${bits.join('<span class="sep"> · </span>')}</div>`;
}

function ensureMap() {
  if (map) return;
  // Touch: dragging off so swipes over the map scroll the page instead of
  // panning it (pinch zoom + marker taps still work).
  map = L.map("map", { dragging: !COARSE });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(map);
}

function draw(data) {
  ensureMap();
  // The map was created inside a hidden container — re-measure now that
  // it's visible or no tiles load.
  map.invalidateSize();
  if (layer) layer.remove();
  layer = L.layerGroup().addTo(map);

  const h = data.hotel;
  L.marker([h.lat, h.lon])
    .addTo(layer)
    .bindPopup(`<b>${esc(h.display_name.split(",")[0])}</b>`)
    .openPopup();

  const pts = [[h.lat, h.lon]];
  spotMarkers = [];
  selected = null;
  for (const items of Object.values(data.groups)) {
    for (const s of items) {
      // Dot color matches the cuisine section's avatar color
      const style = { ...DOT_STYLE, fillColor: avColor(s.group) };
      const dot = L.circleMarker([s.lat, s.lon], style).addTo(layer);
      dot._base = style;
      spotMarkers[s._idx] = dot;
      dot.on("popupopen", () => markRow(s._idx));
      const when = s.drive_s != null ? `${mins(s.drive_s)} drive` : `${s.dist_km} km`;
      const addr = s.address ? (s.address_approx ? "≈ " : "") + s.address : "";
      dot.bindTooltip(`${esc(s.name)} — ${esc(s.group)} · ${when}`);
      const rating =
        s.rating != null
          ? `<br><a class="rating" href="${esc(
              s.google_maps_uri || mapsUrl(s)
            )}" target="_blank" rel="noopener">★ ${s.rating} (${
              s.review_count || 0
            })</a>${s.price ? ` · ${esc(s.price)}` : ""}`
          : "";
      dot.bindPopup(
        `<b>${esc(s.name)}</b> ${openBadge(s)}<br>${esc(s.group)}<br>${when}` +
          rating +
          (addr
            ? `<br><a href="${mapsUrl(s)}" target="_blank" rel="noopener">${esc(addr)}</a>`
            : "") +
          (s.phone ? `<br><a href="${telHref(s.phone)}">${esc(s.phone)}</a>` : "") +
          (s.website
            ? `<br><a href="${esc(s.website)}" target="_blank" rel="noopener">website</a>`
            : "")
      );
      pts.push([s.lat, s.lon]);
    }
  }
  map.fitBounds(pts, { padding: [30, 30] });
}

function markRow(idx) {
  for (const row of groupsEl.querySelectorAll(".spot.selected"))
    row.classList.remove("selected");
  const row = groupsEl.querySelector(`.spot[data-idx="${idx}"]`);
  if (row) {
    row.classList.add("selected");
    // Open the cuisine section when the pick came from the map — the row is
    // invisible inside a collapsed <details>.
    const det = row.closest("details");
    if (det && !det.open) det.open = true;
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function selectSpot(idx) {
  const dot = spotMarkers[idx];
  if (!dot) return;
  if (selected && selected !== dot) selected.setStyle(selected._base);
  selected = dot;
  dot.setStyle(DOT_SELECTED).bringToFront();
  map.flyTo(dot.getLatLng(), Math.max(map.getZoom(), 15), { duration: 0.4 });
  dot.openPopup();
  markRow(idx);
}

function render(data) {
  hotelCard.innerHTML =
    `<div class="name">${esc(data.hotel.display_name.split(",")[0])}</div>` +
    `<div class="addr">${esc(data.hotel.display_name)}</div>`;

  groupsEl.innerHTML = "";
  let idx = 0;
  for (const items of Object.values(data.groups))
    for (const s of items) s._idx = idx++;
  for (const [cuisine, items] of Object.entries(data.groups)) {
    const det = document.createElement("details");
    det.className = "cuisine";
    det.open = Object.keys(data.groups).length <= 8;
    const rows = items
      .map(
        (s) => `<div class="spot" data-idx="${s._idx}">
          <div>
            <div class="rname">${esc(s.name)}</div>
            <div class="meta">${esc(s.cuisine ? s.cuisine.replace(/[;,]/g, ", ") : s.amenity)}${
              s.address
                ? ` · <a href="${mapsUrl(s)}" target="_blank" rel="noopener">` +
                  esc((s.address_approx ? "≈ " : "") + s.address) +
                  "</a>"
                : ""
            }</div>
            ${detailsLine(s)}
          </div>
          <div class="when">
            ${s.drive_s != null ? `<div class="drive">${mins(s.drive_s)}</div>` : ""}
            <div class="far">${s.drive_km != null ? s.drive_km + " km" : s.dist_km + " km"}</div>
            ${openBadge(s)}
          </div>
        </div>`
      )
      .join("");
    det.innerHTML =
      `<summary><span class="avatar" style="--av:${avColor(cuisine)}">` +
      `${cuisineIcon(cuisine)}</span>${esc(cuisine)}` +
      `<span class="n">${items.length}</span></summary>${rows}`;
    groupsEl.appendChild(det);
  }
  resultsEl.hidden = false;
  draw(data);
}

groupsEl.addEventListener("click", (e) => {
  // Taps on maps/tel/website links shouldn't trigger spot-select
  if (e.target.closest("a")) return;
  const row = e.target.closest(".spot");
  if (row && row.dataset.idx != null) selectSpot(Number(row.dataset.idx));
});

// On 429, wait out the Retry-After window with a countdown and resubmit —
// up to 3 automatic retries before giving up.
async function fetchSearch(payload, retries = 0) {
  const resp = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (resp.status === 429) {
    if (retries >= 3) {
      setStatus("Still rate limited — try again shortly.", true);
      return null;
    }
    let secs = Number(resp.headers.get("Retry-After")) || 60;
    secs = Math.min(Math.max(1, Math.ceil(secs)), 120);
    for (; secs > 0; secs--) {
      setStatus(`Rate limited — retrying in ${secs}s…`);
      await new Promise((r) => setTimeout(r, 1000));
    }
    setStatus("Retrying…");
    return fetchSearch(payload, retries + 1);
  }
  if (!resp.ok) {
    const data = await resp.json().catch(() => null);
    setStatus(
      (data && data.detail) || `Search failed (HTTP ${resp.status}).`,
      true
    );
    return null;
  }
  // NDJSON stream: progress events update the status line live, then a
  // single "done" event carries the result payload.
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let result = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line) continue;
      const ev = JSON.parse(line);
      if (ev.t === "progress") setStatus(ev.msg);
      else if (ev.t === "error") {
        setStatus(ev.detail, true);
        return null;
      } else if (ev.t === "done") result = ev.data;
    }
  }
  return result;
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = document.getElementById("query").value.trim();
  if (q) runSearch({ query: q });
});

// "📍" — fills the search box with GPS coords (the backend parses
// "lat, lon" as a location query). Needs HTTPS on iOS Safari.
const locateBtn = document.getElementById("locate");
locateBtn.addEventListener("click", () => {
  const input = document.getElementById("query");
  if (!navigator.geolocation) {
    setStatus(
      "Location isn't available — this browser/context blocks it (iOS needs HTTPS).",
      true
    );
    return;
  }
  locateBtn.disabled = true;
  setStatus("Getting your location…");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      locateBtn.disabled = false;
      input.value = `${pos.coords.latitude.toFixed(5)}, ${pos.coords.longitude.toFixed(5)}`;
      setStatus("Location filled — tap Find restaurants.");
    },
    (err) => {
      locateBtn.disabled = false;
      const why =
        err.code === 1
          ? "location permission was denied — on iPhone check Settings → Privacy & Security → Location Services → Safari, or tap the ⓐ/lock icon in the address bar"
          : err.code === 2
          ? "location unavailable — GPS can't get a fix right now"
          : "timed out waiting for a location fix";
      setStatus(`Couldn't get your location — ${why}.`, true);
    },
    { enableHighAccuracy: false, timeout: 12000, maximumAge: 60000 }
  );
});

async function runSearch(payload) {
  form.querySelector("button[type=submit]").disabled = true;
  resultsEl.hidden = true;
  setStatus("Starting…");
  try {
    const data = await fetchSearch(payload);
    if (!data) return;
    if (!data.count) {
      setStatus(
        data.routed
          ? `No restaurants within a ${data.budget_min}-minute drive of that spot.`
          : "No restaurants found nearby.",
        false
      );
      return;
    }
    render(data);
    setStatus(
      `${data.count} spots within ${data.budget_min} minutes` +
        (data.routed ? "" : " (straight-line — routing unavailable)")
    );
  } catch (err) {
    setStatus(`Request failed: ${err.message}`, true);
  } finally {
    form.querySelector("button[type=submit]").disabled = false;
  }
}
