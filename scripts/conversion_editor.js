"use strict";
const $ = (id) => document.getElementById(id),
  token = location.hash.slice(1) || sessionStorage.getItem("editorToken") || "";
if (token) sessionStorage.setItem("editorToken", token);
history.replaceState(null, "", location.pathname);
let data,
  presets,
  current,
  closed = false,
  running = false;
const labels = {
  highpass_hz: "Low cut (Hz)",
  compressor_ratio: "Compression ratio",
  compressor_threshold_db: "Compressor threshold (dB)",
  ambience_mix: "Ambience (%)",
  ambience_decay_seconds: "Ambience decay (s)",
  ambience_predelay_ms: "Ambience pre-delay (ms)",
  reverb_mix: "Reverb (%)",
  reverb_decay_seconds: "Reverb decay (s)",
  reverb_predelay_ms: "Reverb pre-delay (ms)",
  input_target_lufs: "Input level target (LUFS)",
  input_true_peak_db: "Input peak ceiling (dBTP)",
  target_lufs: "Output target (LUFS)",
  true_peak_db: "Output peak ceiling (dBTP)",
  max_boost_db: "Maximum boost (dB)",
};
const clone = (x) => JSON.parse(JSON.stringify(x)),
  scale = (k) => (k.endsWith("_mix") ? 100 : 1);
async function api(path, value) {
  const response = await fetch(path, {
    method: value === undefined ? "GET" : "POST",
    headers: { "X-Editor-Token": token, "Content-Type": "application/json" },
    body: value === undefined ? undefined : JSON.stringify(value),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "Request failed");
  return result;
}
function text(tag, value) {
  const node = document.createElement(tag);
  node.textContent = value;
  return node;
}
function options(select, values) {
  select.replaceChildren();
  for (const [id, label] of Object.entries(values)) {
    const option = text("option", label);
    option.value = id;
    select.append(option);
  }
}
function presetOptions() {
  const selected = $("base").value,
    reference = $("reference").value;
  const values = Object.fromEntries(
    Object.entries(presets).map(([k, v]) => [k, v.audio.label]),
  );
  options($("base"), values);
  options($("reference"), values);
  if (presets[selected]) $("base").value = selected;
  if (presets[reference]) $("reference").value = reference;
}
function buildControls() {
  $("audio-controls").replaceChildren();
  for (const [key, label] of Object.entries(labels)) {
    const [lo, hi] = data.ranges[key],
      factor = scale(key),
      row = text("div", "");
    row.className = "control";
    const title = text("label", label);
    title.htmlFor = "field-" + key;
    const track = text("div", "");
    track.className = "track";
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = lo * factor;
    slider.max = hi * factor;
    slider.step = key.endsWith("_seconds")
      ? 0.01
      : key === "compressor_ratio"
        ? 0.01
        : key.endsWith("_mix")
          ? 0.1
          : 1;
    slider.id = "field-" + key;
    const marker = text("span", "");
    marker.className = "marker";
    marker.id = "marker-" + key;
    const number = slider.cloneNode();
    number.type = "number";
    number.id = "number-" + key;
    number.setAttribute("aria-label", label);
    const ref = text("span", "");
    ref.className = "ref";
    ref.id = "ref-" + key;
    for (const input of [slider, number])
      input.oninput = () => {
        current.audio[key] = Number(input.value) / factor;
        slider.value = number.value = input.value;
      };
    track.append(slider, marker);
    row.append(title, track, number, ref);
    $("audio-controls").append(row);
  }
}
function reference() {
  const r = presets[$("reference").value];
  if (!r) return;
  for (const key of Object.keys(labels)) {
    const [lo, hi] = data.ranges[key],
      value = r.audio[key];
    $("marker-" + key).style.left = ((value - lo) / (hi - lo)) * 100 + "%";
    $("ref-" + key).textContent =
      "Reference: " + Number((value * scale(key)).toFixed(2));
  }
  $("reference-eq").textContent =
    "Reference EQ: " +
    (r.audio.eq_bands
      .map((b) => `${b.frequency_hz} Hz / ${b.gain_db} dB / Q ${b.q}`)
      .join("; ") || "No EQ bands");
}
function eq() {
  $("eq").replaceChildren();
  current.audio.eq_bands.forEach((band, index) => {
    const row = text("div", "");
    row.className = "eq-row";
    for (const [key, name, min, max, step] of [
      ["frequency_hz", "Frequency (Hz)", 20, 20000, 1],
      ["gain_db", "Gain (dB)", -12, 12, 0.1],
      ["q", "Q", 0.1, 10, 0.1],
    ]) {
      const label = text("label", name),
        input = document.createElement("input");
      Object.assign(input, {
        type: "number",
        min,
        max,
        step,
        value: band[key],
        disabled: !current.audio_enabled,
      });
      input.oninput = () => (band[key] = Number(input.value));
      label.append(input);
      row.append(label);
    }
    const remove = text("button", "Remove");
    remove.disabled = !current.audio_enabled;
    remove.onclick = () => {
      current.audio.eq_bands.splice(index, 1);
      eq();
    };
    row.append(remove);
    $("eq").append(row);
  });
  $("add-eq").disabled =
    !current.audio_enabled || current.audio.eq_bands.length >= 8;
}
function comparison() {
  const table = $("comparison");
  table.replaceChildren();
  const head = document.createElement("tr");
  head.append(text("th", "Setting"));
  for (const p of Object.values(presets))
    head.append(text("th", p.audio.label));
  table.append(head);
  for (const [key, label] of Object.entries(labels)) {
    const row = document.createElement("tr");
    row.append(text("th", label));
    for (const p of Object.values(presets))
      row.append(text("td", Number((p.audio[key] * scale(key)).toFixed(2))));
    table.append(row);
  }
}
function video() {
  const mode = $("video-mode").value;
  current.video.mode = mode;
  if (mode === "none") current.video.resolution = "original";
  $("resolution").value = current.video.resolution;
  $("resolution").disabled = mode === "none";
  $("tone-label").hidden = mode === "none";
  $("picture").hidden = mode !== "adjust";
  $("share").disabled = mode !== "none" || !current.audio_enabled;
  $("video-note").textContent =
    mode === "none"
      ? "Original video is copied. Dolby Vision uses MP4 so its metadata survives; processed audio can remain lossless."
      : "Video is encoded to SDR H.264 at 30 fps. HDR tone mapping can change brightness and color; audio is AAC for sharing.";
}
function fill() {
  for (const key of Object.keys(labels))
    $("field-" + key).value = $("number-" + key).value =
      current.audio[key] * scale(key);
  $("name").value = current.audio.label;
  $("audio-enabled").checked = current.audio_enabled;
  $("video-mode").value = current.video.mode;
  $("resolution").value = current.video.resolution;
  $("tone-map").value = current.video.tone_map;
  $("share").value = String(current.share);
  for (const k of ["brightness", "contrast", "saturation"]) {
    $(k).value = current.video[k];
    $(k + "-value").textContent = current.video[k];
  }
  audioEnabled();
  reference();
  video();
}
function audioEnabled() {
  current.audio_enabled = $("audio-enabled").checked;
  for (const key of Object.keys(labels))
    $("field-" + key).disabled = $("number-" + key).disabled =
      !current.audio_enabled;
  eq();
  video();
}
function settings() {
  const value = clone(current);
  value.audio.label = $("name").value.trim() || "Custom";
  value.share =
    value.video.mode === "none" ? $("share").value === "true" : true;
  return value;
}
function job(state) {
  running = state.running;
  $("convert").disabled = running;
  $("close").disabled = running;
  $("status").textContent = running
    ? "Converting… You can leave this page open."
    : state.errors.length
      ? state.errors.join("\n")
      : state.outputs.length
        ? "Conversion complete. Originals are unchanged."
        : "";
  $("outputs").replaceChildren();
  state.outputs.forEach((path, index) => {
    const li = text("li", path),
      button = text("button", "Show in Finder");
    button.onclick = () =>
      api("/reveal", { index }).catch(
        (error) => ($("status").textContent = error.message),
      );
    li.append(button);
    $("outputs").append(li);
  });
}
async function poll() {
  if (closed) return;
  try {
    const state = await api("/state");
    job(state.job);
  } catch (error) {
    $("status").textContent = error.message;
  }
  if (!closed) setTimeout(poll, 2000);
}
function setup() {
  presets = data.presets;
  presetOptions();
  options($("video-mode"), data.video_modes);
  options($("tone-map"), data.tone_maps);
  $("base").value = presets["jake-preferred"]
    ? "jake-preferred"
    : Object.keys(presets)[0];
  $("reference").value = $("base").value;
  current = clone(presets[$("base").value]);
  current.video = clone(data.video_default);
  buildControls();
  fill();
  comparison();
  for (const path of data.files) $("files").append(text("li", path));
  $("load").onclick = () => {
    current = clone(presets[$("base").value]);
    fill();
  };
  $("reference").onchange = reference;
  $("audio-enabled").onchange = audioEnabled;
  $("add-eq").onclick = () => {
    if (current.audio.eq_bands.length < 8) {
      current.audio.eq_bands.push({ frequency_hz: 1000, gain_db: 0, q: 0.8 });
      eq();
    }
  };
  $("video-mode").onchange = video;
  $("resolution").onchange = () =>
    (current.video.resolution = $("resolution").value);
  $("tone-map").onchange = () => (current.video.tone_map = $("tone-map").value);
  for (const key of ["brightness", "contrast", "saturation"])
    $(key).oninput = () => {
      current.video[key] = Number($(key).value);
      $(key + "-value").textContent = $(key).value;
    };
  $("save").onclick = async () => {
    try {
      const value = settings(),
        saved = await api("/save", value);
      presets[saved.id] = value;
      presetOptions();
      comparison();
      reference();
      $("save-status").textContent =
        "Saved. Available here and next time you open Custom conversion.";
    } catch (error) {
      $("save-status").textContent = error.message;
    }
  };
  $("convert").onclick = async () => {
    if (running) return;
    try {
      $("convert").disabled = true;
      await api("/convert", settings());
      job({ running: true, outputs: [], errors: [] });
    } catch (error) {
      $("status").textContent = error.message;
      $("convert").disabled = false;
    }
  };
  $("close").onclick = async () => {
    try {
      await api("/close", {});
      closed = true;
      $("status").textContent = "Session closed. You can close this tab.";
      for (const button of document.querySelectorAll("button"))
        button.disabled = true;
    } catch (error) {
      $("status").textContent = error.message;
    }
  };
  job(data.job);
  setTimeout(poll, 2000);
}
api("/state")
  .then((value) => {
    data = value;
    setup();
  })
  .catch((error) => {
    $("status").textContent = error.message;
    $("convert").disabled = true;
  });
