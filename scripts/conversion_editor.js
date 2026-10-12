"use strict";
const $ = (id) => document.getElementById(id),
  token = location.hash.slice(1) || sessionStorage.getItem("editorToken") || "";
if (token) sessionStorage.setItem("editorToken", token);
history.replaceState(null, "", location.pathname);
let data,
  presets,
  current,
  closed = false,
  latestJob,
  selectionBusy = false,
  cancelPending = false;
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
  for (const id of [
    "audio-controls",
    "eq",
    "eq-title",
    "add-eq",
    "reference-eq",
  ])
    $(id).hidden = !current.audio_enabled;
  video();
}
function settings() {
  const value = clone(current);
  value.audio.label = $("name").value.trim() || "Custom";
  value.share =
    value.video.mode === "none" ? $("share").value === "true" : true;
  return value;
}
function formatSize(bytes) {
  if (!Number.isFinite(bytes)) return "Unknown size";
  const units = ["bytes", "KB", "MB", "GB"];
  let value = bytes,
    unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}
function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "Unknown duration";
  const rounded = Math.round(seconds),
    minutes = Math.floor(rounded / 60),
    remainder = rounded % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}
function selectionOrigin(origin) {
  const origins = {
    "Finder selection": "Selected in Finder",
    "File picker": "Selected with Choose different files",
    "Provided file paths": "Opened with provided file paths",
  };
  return origins[origin] || origin || "Selected files";
}
function fileName(path) {
  return path ? path.split(/[\\/]/).pop() : "";
}
function renderSelection() {
  document.title = `${data.title} — ${data.files[0]?.name || "No files selected"}`;
  $("selection-origin").textContent = selectionOrigin(data.selection_origin);
  $("files").replaceChildren();
  data.files.forEach((file, index) => {
    const item = document.createElement("li"),
      details = [
        formatSize(file.size_bytes),
        formatDuration(file.duration_seconds),
      ];
    if (file.hdr) details.push("HDR");
    item.append(
      text("strong", file.name),
      text("span", ` — ${details.join(" · ")}`),
      text("div", file.path),
    );
    const remove = text("button", "Remove");
    remove.disabled = latestJob?.task.running || selectionBusy;
    remove.onclick = () => changeSelection("/remove", { index });
    item.append(remove);
    $("files").append(item);
  });
  $("library-warning").textContent = (data.warnings || []).join("\n");
}
function statusMessage(task, progress) {
  if (cancelPending || task.state === data.task_states.CANCELLING)
    return task.errors.length
      ? "Cancellation needs attention. You can retry Cancel.\n" +
          task.errors.join("\n")
      : "Cancelling… Cleanup may take a moment.";
  if (task.state === data.task_states.RUNNING)
    return "Processing… You can leave this page open.";
  if (task.state === data.task_states.CANCELLED)
    return (
      "Processing cancelled. Completed outputs are retained." +
      (task.errors.length ? "\n" + task.errors.join("\n") : "")
    );
  if (task.errors.length) return task.errors.join("\n");
  if (task.state === data.task_states.SUCCEEDED)
    return "Processing complete. Originals are unchanged.";
  if (task.state === data.task_states.FAILED) return "Processing failed.";
  return "Review the files and settings, then start processing.";
}
function renderJob(job) {
  latestJob = job;
  const task = job.task,
    progress = job.progress,
    running = task.running,
    outputs = progress.outputs.length ? progress.outputs : task.outputs;
  $("settings-panel").disabled = running;
  $("choose").disabled = running || selectionBusy;
  $("convert").disabled = running || selectionBusy || !data.files.length;
  $("cancel").disabled = !running || cancelPending;
  $("close").disabled = running || selectionBusy;
  $("processing-title").textContent = running
    ? cancelPending || task.state === data.task_states.CANCELLING
      ? "Cancelling processing"
      : "Processing files"
    : "Ready to process";
  $("status").textContent = statusMessage(task, progress);
  const position = progress.total
      ? `File ${Math.min(progress.index || 1, progress.total)} of ${progress.total}`
      : "",
    elapsed = task.elapsed ? `${Math.round(task.elapsed)} seconds elapsed` : "";
  $("progress-detail").textContent = [
    position,
    fileName(progress.current_file || task.current_file),
    elapsed,
    task.stage,
    ...progress.warnings,
  ]
    .filter(Boolean)
    .join(" · ");
  $("job-log").textContent = task.detail.join("\n");
  $("outputs").replaceChildren();
  outputs.forEach((path, index) => {
    const li = text("li", path),
      button = text("button", "Show in Finder");
    button.onclick = () =>
      api("/reveal", { index }).catch(
        (error) => ($("status").textContent = error.message),
      );
    li.append(button);
    for (const [reportIndex, report] of (
      job.reports?.[index] || []
    ).entries()) {
      const link = text("a", report.label);
      link.href = "#report-view";
      link.className = "report-link";
      link.onclick = async (event) => {
        event.preventDefault();
        try {
          const result = await api("/report", {
            job_id: job.job_id,
            output_index: index,
            report_index: reportIndex,
            name: report.name,
          });
          $("report-title").textContent = result.label;
          $("report-content").textContent = result.content;
          $("report-view").showModal();
        } catch (error) {
          $("status").textContent = error.message;
        }
      };
      li.append(link);
    }
    $("outputs").append(li);
  });
  renderSelection();
}
function applySnapshot(snapshot) {
  if (data && snapshot.selection_revision < data.selection_revision) {
    snapshot = {
      ...snapshot,
      files: data.files,
      selection_origin: data.selection_origin,
      selection_revision: data.selection_revision,
      warnings: data.warnings,
    };
  }
  data = snapshot;
  presets = snapshot.presets;
  renderJob(snapshot.job);
}
async function changeSelection(path, body) {
  if (latestJob?.task.running || selectionBusy) return;
  selectionBusy = true;
  renderJob(latestJob);
  try {
    applySnapshot(await api(path, body));
  } catch (error) {
    $("library-warning").textContent = error.message;
  } finally {
    selectionBusy = false;
    renderJob(latestJob);
  }
}
async function poll() {
  if (closed) return;
  try {
    applySnapshot(await api("/state"));
  } catch (error) {
    $("status").textContent = error.message;
  }
  if (!closed) setTimeout(poll, 2000);
}
async function savePreset() {
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
}
async function startConversion() {
  if (latestJob.task.running || !data.files.length) return;
  try {
    $("convert").disabled = true;
    await api("/convert", {
      settings: settings(),
      selection_revision: data.selection_revision,
    });
    applySnapshot(await api("/state"));
  } catch (error) {
    applySnapshot(await api("/state").catch(() => data));
    $("status").textContent = error.message;
  }
}
async function cancelConversion() {
  if (!latestJob.task.running || cancelPending) return;
  cancelPending = true;
  renderJob(latestJob);
  let requestError;
  try {
    await api("/cancel", { job_id: latestJob.job_id });
    applySnapshot(await api("/state"));
  } catch (error) {
    requestError = error.message;
  } finally {
    cancelPending = false;
    renderJob(latestJob);
    if (requestError) $("status").textContent = requestError;
  }
}
async function closeEditor() {
  try {
    await api("/close", {});
    closed = true;
    $("status").textContent = "Session closed. You can close this tab.";
    for (const button of document.querySelectorAll("button"))
      button.disabled = true;
  } catch (error) {
    $("status").textContent = error.message;
  }
}
function bindControls() {
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
  $("save").onclick = savePreset;
  $("choose").onclick = () => changeSelection("/choose", {});
  $("convert").onclick = startConversion;
  $("cancel").onclick = cancelConversion;
  $("close").onclick = closeEditor;
}
function setup() {
  presets = data.presets;
  $("title").textContent = data.title;
  presetOptions();
  options($("video-mode"), data.video_modes);
  options($("tone-map"), data.tone_maps);
  const initialPreset = Object.entries(presets).find(
    ([, preset]) =>
      JSON.stringify(preset.audio) === JSON.stringify(data.initial.audio),
  );
  $("base").value = initialPreset
    ? initialPreset[0]
    : presets["jake-preferred"]
      ? "jake-preferred"
      : Object.keys(presets)[0];
  $("reference").value = $("base").value;
  current = clone(data.initial);
  if (!current.audio_enabled) $("settings-panel").prepend($("video-section"));
  buildControls();
  fill();
  comparison();
  renderSelection();
  bindControls();
  renderJob(data.job);
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
