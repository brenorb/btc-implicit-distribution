const state = {
  apiRows: [],
  cacheMeta: null,
  expiries: [],
  pyodide: null,
  computeDistribution: null,
  listExpiries: null,
  lastResult: null,
};

const CACHE_KEY = "btc-implicit-distribution:deribit-cache:v1";
const CACHE_TTL_MS = 5 * 60 * 1000;

const els = {
  expiry: document.querySelector("#expiry-select"),
  source: document.querySelector("#source-select"),
  smoothing: document.querySelector("#smoothing-range"),
  smoothingOutput: document.querySelector("#smoothing-output"),
  minRange: document.querySelector("#min-range"),
  maxRange: document.querySelector("#max-range"),
  rangeOutput: document.querySelector("#range-output"),
  clip: document.querySelector("#clip-checkbox"),
  envelope: document.querySelector("#envelope-checkbox"),
  refresh: document.querySelector("#refresh-button"),
  spot: document.querySelector("#spot-value"),
  mean: document.querySelector("#mean-value"),
  median: document.querySelector("#median-value"),
  aboveSpot: document.querySelector("#above-spot-value"),
  annualized: document.querySelector("#annualized-value"),
  band1090: document.querySelector("#band-10-90"),
  band2575: document.querySelector("#band-25-75"),
  chartTitle: document.querySelector("#chart-title"),
  status: document.querySelector("#status-pill"),
  quality: document.querySelector("#quality-pill"),
  stripMarkers: document.querySelector("#strip-markers"),
  stripMarkerTemplate: document.querySelector("#strip-marker-template"),
};

function setStatus(message, kind = "loading") {
  els.status.textContent = message;
  els.status.className = `status-pill ${kind}`;
}

function formatUsd(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

function formatPercent(value) {
  return new Intl.NumberFormat("pt-BR", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);
}

function formatRelativeMinutes(timestamp) {
  const deltaMs = Date.now() - timestamp;
  const deltaMinutes = Math.max(Math.round(deltaMs / 60000), 0);
  if (deltaMinutes < 1) {
    return "agora";
  }
  if (deltaMinutes === 1) {
    return "1 min";
  }
  return `${deltaMinutes} min`;
}

function readRangeBounds() {
  let min = Number(els.minRange.value) / 100;
  let max = Number(els.maxRange.value) / 100;
  if (min >= max) {
    if (document.activeElement === els.minRange) {
      max = min + 0.1;
      els.maxRange.value = Math.min(max * 100, 250);
    } else {
      min = max - 0.1;
      els.minRange.value = Math.max(min * 100, 30);
    }
  }
  min = Number(els.minRange.value) / 100;
  max = Number(els.maxRange.value) / 100;
  return { min, max };
}

function syncOutputs() {
  els.smoothingOutput.textContent = els.smoothing.value;
  const { min, max } = readRangeBounds();
  els.rangeOutput.textContent = `${Math.round(min * 100)}% a ${Math.round(max * 100)}%`;
}

async function loadPythonEngine() {
  setStatus("Carregando Pyodide", "loading");
  const pyodide = await globalThis.loadPyodide();
  const engineSource = await fetch("./py/implied_distribution.py").then((response) => response.text());
  await pyodide.runPythonAsync(engineSource);
  state.pyodide = pyodide;
  state.computeDistribution = pyodide.globals.get("compute_distribution_from_json");
  state.listExpiries = pyodide.globals.get("list_expiries_from_json");
}

function readCachedRows() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed.rows) || typeof parsed.savedAt !== "number") {
      return null;
    }
    return parsed;
  } catch (error) {
    console.warn("Nao foi possivel ler o cache local.", error);
    return null;
  }
}

function writeCachedRows(rows) {
  try {
    localStorage.setItem(
      CACHE_KEY,
      JSON.stringify({
        savedAt: Date.now(),
        rows,
      }),
    );
  } catch (error) {
    console.warn("Nao foi possivel gravar o cache local.", error);
  }
}

async function fetchApiRows({ force = false } = {}) {
  const cached = readCachedRows();
  const cacheIsFresh = cached && Date.now() - cached.savedAt <= CACHE_TTL_MS;

  if (!force && cacheIsFresh) {
    state.apiRows = cached.rows;
    state.cacheMeta = { source: "cache", savedAt: cached.savedAt };
    return;
  }

  setStatus(force ? "Atualizando Deribit" : "Buscando Deribit", "loading");

  try {
    const response = await fetch(
      "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option",
      { cache: "no-store" },
    );
    if (!response.ok) {
      throw new Error(`Deribit retornou ${response.status}`);
    }
    const payload = await response.json();
    state.apiRows = payload.result;
    state.cacheMeta = { source: "network", savedAt: Date.now() };
    writeCachedRows(payload.result);
  } catch (error) {
    if (cached) {
      state.apiRows = cached.rows;
      state.cacheMeta = { source: "stale-cache", savedAt: cached.savedAt };
      return;
    }
    throw error;
  }
}

function pyProxyToJs(proxy) {
  const result = proxy.toJs({ dict_converter: Object.fromEntries });
  proxy.destroy();
  return result;
}

function populateExpiries() {
  const previousValue = els.expiry.value;
  const proxy = state.listExpiries(JSON.stringify(state.apiRows));
  state.expiries = pyProxyToJs(proxy);
  els.expiry.innerHTML = state.expiries
    .map((expiry) => `<option value="${expiry.code}">${expiry.label}</option>`)
    .join("");

  const hasPreviousValue = state.expiries.some((expiry) => expiry.code === previousValue);
  if (hasPreviousValue) {
    els.expiry.value = previousValue;
    return;
  }

  const todayIso = new Date().toISOString().slice(0, 10);
  const nextFutureExpiry = state.expiries.find((expiry) => expiry.iso > todayIso);
  if (nextFutureExpiry) {
    els.expiry.value = nextFutureExpiry.code;
  }
}

function displayQuality(result) {
  const quality = result.quality;
  const freshness =
    state.cacheMeta?.source === "cache"
      ? `Cache local · ${formatRelativeMinutes(state.cacheMeta.savedAt)}`
      : state.cacheMeta?.source === "stale-cache"
        ? `Cache local expirado · ${formatRelativeMinutes(state.cacheMeta.savedAt)}`
        : "Deribit ao vivo";

  els.quality.textContent =
    `${freshness} · ` +
    `Calls ${quality.call_real_strikes}/${quality.call_real_strikes + quality.call_interpolated_strikes} · ` +
    `Puts ${quality.put_real_strikes}/${quality.put_real_strikes + quality.put_interpolated_strikes}`;
}

function filterPoints(result) {
  const { min, max } = readRangeBounds();
  const lowerBound = result.spot_price * min;
  const upperBound = result.spot_price * max;
  return result.points.filter((point) => point.strike >= lowerBound && point.strike <= upperBound);
}

function renderStrip(result) {
  els.stripMarkers.innerHTML = "";
  const stops = [
    { label: "10%", value: result.band_10 },
    { label: "25%", value: result.band_25 },
    { label: "50%", value: result.median_price },
    { label: "75%", value: result.band_75 },
    { label: "90%", value: result.band_90 },
    { label: "Media", value: result.mean_price },
  ];
  const minValue = result.band_10;
  const maxValue = result.band_90;

  stops.forEach((stop) => {
    const node = els.stripMarkerTemplate.content.firstElementChild.cloneNode(true);
    const offset = ((stop.value - minValue) / Math.max(maxValue - minValue, 1)) * 100;
    node.style.left = `${Math.max(0, Math.min(offset, 100))}%`;
    node.querySelector(".strip-label").textContent = `${stop.label} · ${formatUsd(stop.value)}`;
    els.stripMarkers.appendChild(node);
  });
}

function renderMetrics(result) {
  els.spot.textContent = formatUsd(result.spot_price);
  els.mean.textContent = formatUsd(result.mean_price);
  els.median.textContent = formatUsd(result.median_price);
  els.aboveSpot.textContent = formatPercent(result.probability_above_spot);
  els.band1090.textContent = `${formatUsd(result.band_10)} - ${formatUsd(result.band_90)}`;
  els.band2575.textContent = `${formatUsd(result.band_25)} - ${formatUsd(result.band_75)}`;
  els.annualized.textContent =
    Number.isFinite(result.annualized_return) ? formatPercent(result.annualized_return) : "n/a";
  els.chartTitle.textContent = `${result.expiry.label} · ${result.expiry.days_to_expiry} dias ate o vencimento`;
}

function renderChart(result) {
  const points = filterPoints(result);
  const showEnvelope = els.envelope.checked;
  const x = points.map((point) => point.strike);
  const y = points.map((point) => point.probability);
  const lower = points.map((point) => point.lower_probability);
  const upper = points.map((point) => point.upper_probability);
  const yMax = Math.max(...upper, ...y, 0.00001) * 1.15;

  const traces = [];
  if (showEnvelope) {
    traces.push({
      x,
      y: lower,
      mode: "lines",
      line: { color: "rgba(24, 33, 27, 0)" },
      hoverinfo: "skip",
      showlegend: false,
    });
    traces.push({
      x,
      y: upper,
      mode: "lines",
      fill: "tonexty",
      fillcolor: "rgba(157, 95, 42, 0.14)",
      line: { color: "rgba(157, 95, 42, 0.24)", width: 1 },
      name: "Envelope bid/ask",
      hovertemplate: "Strike %{x:$,.0f}<br>Envelope %{y:.2%}<extra></extra>",
    });
  }

  traces.push({
    x,
    y,
    mode: "lines",
    line: { color: "#1f4a31", width: 4, shape: "spline", smoothing: 0.65 },
    name: "Estimativa central",
    hovertemplate: "Strike %{x:$,.0f}<br>Probabilidade %{y:.2%}<extra></extra>",
  });

  const layout = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    margin: { l: 56, r: 24, t: 22, b: 56 },
    hovermode: "x unified",
    showlegend: showEnvelope,
    legend: {
      orientation: "h",
      y: 1.08,
      x: 0,
      bgcolor: "rgba(255,255,255,0)",
      font: { family: "IBM Plex Sans", size: 12, color: "#18211b" },
    },
    xaxis: {
      title: "Preco de vencimento (USD)",
      gridcolor: "rgba(24, 33, 27, 0.08)",
      zeroline: false,
      tickprefix: "$",
      titlefont: { family: "IBM Plex Sans Condensed", size: 12, color: "#5e685f" },
    },
    yaxis: {
      title: "Probabilidade",
      gridcolor: "rgba(24, 33, 27, 0.08)",
      zeroline: false,
      tickformat: ".0%",
      range: [0, yMax],
      titlefont: { family: "IBM Plex Sans Condensed", size: 12, color: "#5e685f" },
    },
    font: { family: "IBM Plex Sans", size: 13, color: "#18211b" },
    shapes: [
      {
        type: "line",
        x0: result.spot_price,
        x1: result.spot_price,
        y0: 0,
        y1: yMax,
        line: { color: "#9d5f2a", width: 2, dash: "dot" },
      },
      {
        type: "line",
        x0: result.mean_price,
        x1: result.mean_price,
        y0: 0,
        y1: yMax,
        line: { color: "#244532", width: 2, dash: "dash" },
      },
    ],
    annotations: [
      {
        x: result.spot_price,
        y: yMax,
        text: "Spot",
        showarrow: false,
        yanchor: "bottom",
        font: { family: "IBM Plex Sans Condensed", size: 11, color: "#9d5f2a" },
      },
      {
        x: result.mean_price,
        y: yMax,
        text: "Media implicita",
        showarrow: false,
        yanchor: "bottom",
        font: { family: "IBM Plex Sans Condensed", size: 11, color: "#244532" },
      },
    ],
  };

  Plotly.react("chart", traces, layout, {
    responsive: true,
    displayModeBar: false,
  });
}

async function recompute() {
  if (!state.computeDistribution || !state.apiRows.length) {
    return;
  }

  try {
    syncOutputs();
    setStatus("Calculando distribuicao", "loading");
    const proxy = state.computeDistribution(
      JSON.stringify(state.apiRows),
      els.expiry.value,
      els.source.value,
      Number(els.smoothing.value),
      els.clip.checked,
    );
    const result = pyProxyToJs(proxy);
    state.lastResult = result;
    renderMetrics(result);
    displayQuality(result);
    renderChart(result);
    renderStrip(result);
    setStatus("Pronto", "ready");
  } catch (error) {
    console.error(error);
    setStatus("Falha no calculo", "error");
    els.quality.textContent = error.message;
  }
}

async function refreshData({ force = false } = {}) {
  try {
    await fetchApiRows({ force });
    populateExpiries();
    await recompute();
  } catch (error) {
    console.error(error);
    setStatus("Falha ao carregar dados", "error");
    els.quality.textContent = error.message;
  }
}

async function bootstrap() {
  syncOutputs();
  [
    els.expiry,
    els.source,
    els.smoothing,
    els.minRange,
    els.maxRange,
    els.clip,
    els.envelope,
  ].forEach((element) => {
    element.addEventListener("input", () => {
      syncOutputs();
      if (state.lastResult && (element === els.minRange || element === els.maxRange || element === els.envelope)) {
        renderChart(state.lastResult);
        renderStrip(state.lastResult);
        return;
      }
      recompute();
    });
    element.addEventListener("change", () => {
      syncOutputs();
      recompute();
    });
  });

  els.refresh.addEventListener("click", () => refreshData({ force: true }));

  try {
    await loadPythonEngine();
    await refreshData();
  } catch (error) {
    console.error(error);
    setStatus("Falha ao iniciar", "error");
    els.quality.textContent = error.message;
  }
}

bootstrap();
