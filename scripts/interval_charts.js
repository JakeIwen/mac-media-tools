"use strict";
{
  const NS = "http://www.w3.org/2000/svg";
  function svgElement(tag, attributes = {}, content) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (content !== undefined) node.textContent = content;
    return node;
  }
  function htmlElement(tag, content, className) {
    const node = document.createElement(tag);
    if (content !== undefined) node.textContent = content;
    if (className) node.className = className;
    return node;
  }
  function clock(seconds) {
    const whole = Math.max(0, Math.floor(seconds));
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
  }
  function numeric(value, digits = 1) {
    return Number.isFinite(value) ? value.toFixed(digits) : "unvoiced";
  }
  function chartRoot(container, height, title) {
    const scroll = htmlElement("div", undefined, "chart-scroll");
    const svg = svgElement("svg", { viewBox: `0 0 800 ${height}`, role: "group", "aria-label": title });
    svg.append(svgElement("title", {}, title));
    scroll.append(svg);
    container.append(scroll);
    return svg;
  }
  function tableView(container, headers, rows) {
    const details = htmlElement("details"), scroll = htmlElement("div", undefined, "chart-table");
    details.append(htmlElement("summary", "View data table"), scroll);
    details.addEventListener("toggle", () => {
      if (!details.open || scroll.childNodes.length) return;
      const table = htmlElement("table"), head = htmlElement("thead"), body = htmlElement("tbody");
      const heading = htmlElement("tr");
      for (const value of headers) {
        const cell = htmlElement("th", value);
        cell.scope = "col";
        heading.append(cell);
      }
      head.append(heading);
      for (const row of rows) {
        const tr = htmlElement("tr");
        for (const value of row) tr.append(htmlElement("td", value));
        body.append(tr);
      }
      table.append(head, body);
      scroll.append(table);
    });
    container.append(details);
  }
  function histogramRow(svg, item, index, readout) {
    const y = 26 + index * 34, share = Math.max(0, Math.min(1, item.share));
    const label = `${item.short}${item.direction === "unison" ? "" : ` ${item.direction}`}`;
    const detail = `${item.name}, ${item.direction}: ${(share * 100).toFixed(1)} % (${item.frames} frames)`;
    const row = svgElement("g", { class: "bar-row", tabindex: "0", role: "img", "aria-label": detail });
    row.append(svgElement("title", {}, detail));
    row.append(svgElement("rect", { x: 0, y: y - 16, width: 800, height: 32, class: "hit" }));
    row.append(svgElement("text", { x: 130, y: y + 4, "text-anchor": "end" }, label));
    row.append(svgElement("rect", { x: 144, y: y - 9, width: 570 * share, height: 18, rx: 3, class: "bar" }));
    row.append(svgElement("text", { x: 725, y: y + 4, class: "direct-label" }, `${(share * 100).toFixed(1)} %`));
    row.addEventListener("pointermove", () => { readout.textContent = detail; });
    row.addEventListener("focus", () => { readout.textContent = detail; });
    svg.append(row);
  }
  function renderHistogram(container, histogram) {
    container.replaceChildren();
    if (!histogram.length) {
      container.append(htmlElement("p", "No jointly voiced frames to count.", "note"));
      return;
    }
    const height = 48 + histogram.length * 34;
    const svg = chartRoot(container, height, "Share of jointly voiced frames by stabilized interval");
    const readout = htmlElement("p", "Hover or focus a bar for its interval and frame count.", "chart-readout");
    for (const [index, item] of histogram.entries()) histogramRow(svg, item, index, readout);
    const y = height - 15;
    svg.append(svgElement("line", { x1: 144, y1: y - 14, x2: 714, y2: y - 14, class: "grid" }));
    for (const percentage of [0, 25, 50, 75, 100]) {
      svg.append(svgElement("text", { x: 144 + 5.7 * percentage, y, "text-anchor": "middle" }, `${percentage} %`));
    }
    container.append(readout);
    tableView(container, ["Interval", "Direction", "Semitones", "Frames", "Share"], histogram.map((item) =>
      [item.name, item.direction, item.semitones, item.frames, `${(item.share * 100).toFixed(1)} %`]));
  }
  function plotScale(points, analysis) {
    const frequencies = points.flatMap((p) => [p.reference_hz, p.comparison_hz])
      .filter((v) => Number.isFinite(v) && v > 0);
    const cents = points.flatMap((p) => [p.cents, p.smoothed_cents]).filter(Number.isFinite);
    const lowHz = Math.min(110, ...frequencies), highHz = Math.max(440, ...frequencies);
    const low = Math.floor(Math.min(0, ...cents) / 100) * 100 - 100;
    const high = Math.ceil(Math.max(0, ...cents) / 100) * 100 + 100;
    const end = Math.max(0.01, analysis.reference.duration, ...points.map((p) => p.time));
    const logLow = Math.log2(lowHz) - 0.15, logHigh = Math.log2(highHz) + 0.15;
    return {
      end, low, high, lowHz, highHz,
      x: (time) => 65 + time / end * 600,
      hz: (value) => 195 - (Math.log2(value) - logLow) / (logHigh - logLow) * 150,
      cents: (value) => 395 - (value - low) / (high - low) * 150,
    };
  }
  function pitchAxes(svg, scale) {
    svg.append(svgElement("text", { x: 10, y: 23, class: "chart-heading" }, "Pitch · Hz (log scale)"));
    for (let hz = 27.5; hz <= scale.highHz * 1.1; hz *= 2) {
      if (hz < scale.lowHz / 1.1) continue;
      const y = scale.hz(hz);
      svg.append(svgElement("line", { x1: 65, x2: 665, y1: y, y2: y, class: "grid" }));
      svg.append(svgElement("text", { x: 56, y: y + 4, "text-anchor": "end" }, String(hz)));
    }
  }
  function intervalAxes(svg, scale) {
    svg.append(svgElement("text", { x: 10, y: 228, class: "chart-heading" }, "Interval · cents (100 cents = 1 semitone)"));
    const step = Math.max(1, Math.ceil((scale.high - scale.low) / 100 / 60)) * 100;
    const labelStep = Math.max(step, Math.ceil((scale.high - scale.low) / 600) * 100);
    for (let value = scale.low, i = 0; value < scale.high; value += step, i++) {
      const top = scale.cents(Math.min(scale.high, value + step)), bottom = scale.cents(value);
      if (i % 2 === 0) svg.append(svgElement("rect", { x: 65, y: top, width: 600, height: bottom - top, class: "band" }));
    }
    for (let value = Math.ceil(scale.low / labelStep) * labelStep; value <= scale.high; value += labelStep) {
      const y = scale.cents(value);
      svg.append(svgElement("line", { x1: 65, x2: 665, y1: y, y2: y, class: "grid" }));
      svg.append(svgElement("text", { x: 56, y: y + 4, "text-anchor": "end" }, String(value)));
    }
    for (let i = 0; i <= 5; i++) {
      const time = scale.end * i / 5, x = scale.x(time);
      svg.append(svgElement("text", { x, y: 420, "text-anchor": "middle" }, clock(time)));
    }
    svg.append(svgElement("text", { x: 365, y: 443, "text-anchor": "middle" }, "Reference time · mm:ss"));
  }
  function tracePath(points, key, x, y) {
    const parts = [];
    let previous = false;
    for (const point of points) {
      const valid = Number.isFinite(point[key]) && (key.endsWith("hz") ? point[key] > 0 : true);
      if (valid) parts.push(`${previous ? "L" : "M"}${x(point.time).toFixed(2)},${y(point[key]).toFixed(2)}`);
      previous = valid;
    }
    return parts.join(" ");
  }
  function drawTrace(svg, points, key, scale, className, label, labelY) {
    const project = key.endsWith("hz") ? scale.hz : scale.cents;
    const path = svgElement("path", { d: tracePath(points, key, scale.x, project), class: `trace ${className}` });
    path.append(svgElement("title", {}, label));
    svg.append(path);
    svg.append(svgElement("line", { x1: 682, x2: 710, y1: labelY, y2: labelY, class: `trace ${className}` }));
    svg.append(svgElement("text", { x: 718, y: labelY + 4, class: "direct-label" }, label));
  }
  function legend(container) {
    const legend = htmlElement("div", undefined, "chart-legend");
    for (const [className, label] of [["reference", "Reference pitch"], ["comparison", "Comparison pitch"],
      ["raw", "Raw interval"], ["smoothed", "Smoothed interval"]]) {
      const item = htmlElement("span"), key = svgElement("svg", { viewBox: "0 0 28 14", "aria-hidden": "true" });
      key.append(svgElement("line", { x1: 0, y1: 7, x2: 28, y2: 7, class: `trace ${className}` }));
      item.append(key, document.createTextNode(label));
      legend.append(item);
    }
    container.append(legend);
  }
  function pointDescription(point) {
    return `${point.time.toFixed(2)} s · reference ${numeric(point.reference_hz)} Hz · ` +
      `comparison ${numeric(point.comparison_hz)} Hz · raw ${numeric(point.cents)} cents · ` +
      `smoothed ${numeric(point.smoothed_cents)} cents · label ${numeric(point.semitones, 0)} semitones`;
  }
  function timelineInteraction(svg, points, scale, readout) {
    const hair = svgElement("line", { y1: 40, y2: 398, class: "crosshair", visibility: "hidden" });
    const target = svgElement("rect", { x: 65, y: 40, width: 600, height: 358, class: "hit", tabindex: "0",
      role: "slider", "aria-label": "Inspect timeline; use arrow keys to move through frames",
      "aria-valuemin": "0", "aria-valuemax": String(points.length - 1), "aria-valuenow": "0" });
    let index = 0;
    const show = (next) => {
      index = Math.max(0, Math.min(points.length - 1, next));
      const point = points[index], description = pointDescription(point), x = scale.x(point.time);
      hair.setAttribute("x1", x);
      hair.setAttribute("x2", x);
      hair.setAttribute("visibility", "visible");
      target.setAttribute("aria-valuenow", index);
      target.setAttribute("aria-valuetext", description);
      readout.textContent = description;
    };
    target.addEventListener("pointermove", (event) => {
      const bounds = svg.getBoundingClientRect(), x = (event.clientX - bounds.left) / bounds.width * 800;
      const time = (x - 65) / 600 * scale.end;
      let nearest = 0;
      for (let i = 1; i < points.length; i++) {
        if (Math.abs(points[i].time - time) < Math.abs(points[nearest].time - time)) nearest = i;
      }
      show(nearest);
    });
    target.addEventListener("focus", () => show(index));
    target.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? points.length - 1 :
        index + (event.key === "ArrowRight" ? 1 : -1);
      show(next);
    });
    svg.append(hair, target);
  }
  function renderTimeline(container, analysis) {
    container.replaceChildren();
    const points = analysis.timeline;
    if (!points.length) {
      container.append(htmlElement("p", "No timeline frames.", "note"));
      return;
    }
    legend(container);
    const svg = chartRoot(container, 455, "Aligned reference and comparison pitch, raw and smoothed interval over reference time");
    const scale = plotScale(points, analysis);
    pitchAxes(svg, scale);
    intervalAxes(svg, scale);
    drawTrace(svg, points, "reference_hz", scale, "reference", "Reference", 62);
    drawTrace(svg, points, "comparison_hz", scale, "comparison", "Comparison", 86);
    drawTrace(svg, points, "cents", scale, "raw", "Raw", 262);
    drawTrace(svg, points, "smoothed_cents", scale, "smoothed", "Smoothed", 286);
    const readout = htmlElement("p", "Hover or focus the timeline to inspect a frame; use the arrow keys to move.", "chart-readout");
    timelineInteraction(svg, points, scale, readout);
    container.append(readout);
    tableView(container, ["Reference time (s)", "Reference (Hz)", "Comparison (Hz)", "Raw (cents)", "Smoothed (cents)", "Semitones"],
      points.map((point) => [point.time.toFixed(2), numeric(point.reference_hz), numeric(point.comparison_hz),
        numeric(point.cents), numeric(point.smoothed_cents), numeric(point.semitones, 0)]));
  }
  globalThis.IntervalCharts = { renderHistogram, renderTimeline };
}
