"use strict";

const $ = (id) => document.getElementById(id);
const COLORS = { cpu: "#63b7ec", memory: "#b78be6", gpu: "#78cb99", disk: "#e6ac74", network: "#6d9cf6", teal: "#64cab7" };
const RESOURCE_IDS = ["cpu", "memory", "gpu", "disk", "network"];
const RESOURCE_NAMES = { cpu: "CPU", memory: "Bộ nhớ", gpu: "GPU", disk: "Ổ đĩa", network: "Mạng" };
const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = {
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 3v3m6-3v3M9 18v3m6-3v3M3 9h3m-3 6h3m12-6h3m-3 6h3"/>',
  memory: '<rect x="3" y="7" width="18" height="10" rx="2"/><path d="M7 10v4m5-4v4m5-4v4M6 17v3m4-3v3m4-3v3m4-3v3"/>',
  gpu: '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="10" cy="12" r="3"/><path d="M16 10h2m-2 4h2M7 18v3m4-3v3m4-3v3"/>',
  disk: '<path d="M5 4h14l3 12v3a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-3L5 4Z"/><path d="M2 16h20m-5 2h.01M6 8h12"/>',
  network: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18ZM5 7h14M5 17h14"/>',
  server: '<rect x="4" y="3" width="16" height="8" rx="2"/><rect x="4" y="14" width="16" height="7" rx="2"/><path d="M8 7h.01M8 17.5h.01M12 7h4M12 17.5h4"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  play: '<path d="m8 5 11 7-11 7V5Z"/>',
  pause: '<path d="M9 5v14M15 5v14"/>'
};
function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function svgEl(tag, attrs = {}) { const node = document.createElementNS(SVG_NS, tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); return node; }
function icon(name) { const node = svgEl("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }); node.innerHTML = ICONS[name] || ICONS.server; return node; }
function setText(id, text) { $(id).textContent = text; }
function valid(value) { return typeof value === "number" && Number.isFinite(value); }
function number(value, digits = 0) { return valid(value) ? value.toLocaleString("vi-VN", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—"; }
function percent(value, digits = 1) { return valid(value) ? `${number(value, digits)}%` : "—"; }
function bytes(value, digits = 1) { if (!valid(value)) return "—"; const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]; let n = Math.max(0, value), unit = 0; while (n >= 1024 && unit < units.length - 1) { n /= 1024; unit++; } return `${number(n, unit === 0 ? 0 : digits)} ${units[unit]}`; }
function rate(value) { return valid(value) ? `${bytes(value)}/s` : "—"; }
function frequency(value) { return valid(value) && value > 0 ? `${number(value / 1000, 2)} GHz` : "—"; }
function temperature(value) { return valid(value) ? `${number(value, 1)} °C` : "Không có dữ liệu"; }
function uptime(value) { if (!valid(value)) return "—"; const s = Math.floor(value); const days = Math.floor(s / 86400); const h = Math.floor(s % 86400 / 3600); const m = Math.floor(s % 3600 / 60); const secs = s % 60; return `${days ? `${days} ngày · ` : ""}${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(secs).padStart(2, "0")}`; }
function time(value) { return valid(value) ? new Date(value * 1000).toLocaleTimeString("vi-VN", { hour12: false }) : "—"; }
function dateTime(value) { return valid(value) ? new Date(value * 1000).toLocaleString("vi-VN", { hour12: false }) : "—"; }
function meter(value, color) { const node = el("div", "resource-meter"); if (color) node.style.setProperty("--resource-color", color); const fill = el("span"); fill.style.width = `${valid(value) ? Math.max(0, Math.min(100, value)) : 0}%`; node.append(fill); return node; }
function labelValue(label, value, className) { const node = el("div", className); node.append(el("dt", "", label), el("dd", "", value)); return node; }

let chartCount = 0;
class LiveChart {
  constructor(host, { compact = false } = {}) {
    this.host = host; this.compact = compact; this.id = `chart-${++chartCount}`; this.data = []; this.series = []; this.max = 100; this.pointer = null;
    this.w = 900; this.h = compact ? 100 : 280; this.left = compact ? 0 : 12; this.right = compact ? 900 : 847; this.top = compact ? 5 : 8; this.bottom = compact ? 99 : 272;
    this.svg = svgEl("svg", { viewBox: `0 0 ${this.w} ${this.h}`, preserveAspectRatio: "none", class: "chart-svg", "aria-hidden": "true" });
    this.defs = svgEl("defs"); this.grid = svgEl("g"); this.lines = svgEl("g"); this.hover = svgEl("g"); this.svg.append(this.defs, this.grid, this.lines, this.hover); host.append(this.svg);
    if (!compact) {
      this.empty = el("div", "chart-empty", "Đang chờ dữ liệu…"); this.tooltip = el("div", "chart-tooltip"); this.tooltip.hidden = true; host.append(this.empty, this.tooltip);
      host.addEventListener("pointermove", (event) => { const rect = host.getBoundingClientRect(); this.pointer = Math.max(0, Math.min(1, ((event.clientX - rect.left) / rect.width * this.w - this.left) / (this.right - this.left))); this.showHover(); });
      host.addEventListener("pointerleave", () => { this.pointer = null; this.hover.replaceChildren(); this.tooltip.hidden = true; });
    }
  }
  update(data, series, { max = 100, formatter = (value) => percent(value), axisFormatter = (value) => percent(value, 0), unavailable = "Chưa có dữ liệu cho tài nguyên này" } = {}) {
    if (!this.compact) { this.w = Math.max(240, this.host.clientWidth || 900); this.h = this.host.clientHeight || 280; this.left = 4; this.right = this.w - 61; this.bottom = this.h - 8; this.svg.setAttribute("viewBox", `0 0 ${this.w} ${this.h}`); }
    this.series = series; this.formatter = formatter; this.data = data; this.max = Math.max(1, max); this.end = data.length ? data[data.length - 1].timestamp : Date.now() / 1000; this.start = this.end - 60;
    this.grid.replaceChildren(); this.lines.replaceChildren(); this.defs.replaceChildren();
    const y = (value) => this.bottom - Math.max(0, Math.min(this.max, value)) / this.max * (this.bottom - this.top);
    const x = (stamp) => this.left + (stamp - this.start) / 60 * (this.right - this.left);
    if (!this.compact) {
      for (let i = 0; i <= 4; i++) { const pos = this.top + (this.bottom - this.top) * i / 4; this.grid.append(svgEl("line", { x1: this.left, y1: pos, x2: this.right, y2: pos, class: "chart-grid-line" })); const text = svgEl("text", { x: this.right + 9, y: pos + 3, class: "chart-axis-label" }); text.textContent = axisFormatter(this.max * (1 - i / 4)); this.grid.append(text); }
      for (let i = 0; i <= 12; i++) { const pos = this.left + (this.right - this.left) * i / 12; this.grid.append(svgEl("line", { x1: pos, y1: this.top, x2: pos, y2: this.bottom, class: "chart-grid-line" })); }
    }
    let count = 0;
    series.forEach((s, index) => {
      const gradientId = `${this.id}-gradient-${index}`; const gradient = svgEl("linearGradient", { id: gradientId, x1: "0", y1: "0", x2: "0", y2: "1" }); gradient.append(svgEl("stop", { offset: "0%", "stop-color": s.color, "stop-opacity": this.compact ? .3 : .23 }), svgEl("stop", { offset: "100%", "stop-color": s.color, "stop-opacity": 0 })); this.defs.append(gradient);
      let segment = []; const draw = () => { if (!segment.length) return; const path = segment.map((point, i) => `${i ? "L" : "M"}${point[0].toFixed(2)},${point[1].toFixed(2)}`).join(" "); if (segment.length > 1) { this.lines.append(svgEl("path", { d: `${path} L${segment[segment.length - 1][0]},${this.bottom} L${segment[0][0]},${this.bottom} Z`, fill: `url(#${gradientId})`, class: "chart-area" })); this.lines.append(svgEl("path", { d: path, stroke: s.color, class: "chart-line" })); } else { this.lines.append(svgEl("circle", { cx: segment[0][0], cy: segment[0][1], r: this.compact ? 2 : 3, fill: s.color })); } segment = []; };
      data.forEach((point) => { if (point.timestamp < this.start || point.timestamp > this.end) return; const value = point[s.key]; if (valid(value)) { segment.push([x(point.timestamp), y(value)]); count++; } else draw(); }); draw();
    });
    if (!this.compact) { this.empty.textContent = count ? "" : unavailable; this.empty.hidden = count > 0; this.showHover(); }
  }
  showHover() {
    if (this.compact || this.pointer === null || !this.data.length) return;
    const target = this.start + this.pointer * 60; let nearest = null;
    this.data.forEach((entry) => { if (entry.timestamp >= this.start && this.series.some((s) => valid(entry[s.key])) && (!nearest || Math.abs(entry.timestamp - target) < Math.abs(nearest.timestamp - target))) nearest = entry; });
    this.hover.replaceChildren(); this.tooltip.replaceChildren(); if (!nearest) { this.tooltip.hidden = true; return; }
    const x = this.left + (nearest.timestamp - this.start) / 60 * (this.right - this.left); this.hover.append(svgEl("line", { x1: x, y1: this.top, x2: x, y2: this.bottom, class: "chart-cursor" })); this.tooltip.append(el("span", "tooltip-time", time(nearest.timestamp)));
    this.series.forEach((s) => { const value = nearest[s.key]; if (!valid(value)) return; const y = this.bottom - Math.max(0, Math.min(this.max, value)) / this.max * (this.bottom - this.top); this.hover.append(svgEl("circle", { cx: x, cy: y, r: 4, fill: s.color, stroke: "#eaf5ff", "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" })); const row = el("span", "tooltip-row"); const dot = el("i", "legend-dot"); dot.style.background = s.color; row.append(dot, el("span", "", s.name), el("strong", "", this.formatter(value))); this.tooltip.append(row); });
    const width = this.host.getBoundingClientRect().width; const inset = Math.min(100, width / 3); this.tooltip.style.left = `${Math.max(inset, Math.min(width - inset, x / this.w * width))}px`; this.tooltip.hidden = false;
  }
}

const overviewChart = new LiveChart($("overview-chart"));
const networkChart = new LiveChart($("network-overview-chart"));
const detailChart = new LiveChart($("detail-chart"));
const summaryCards = {}, resourceButtons = {};
RESOURCE_IDS.forEach((id) => {
  const summary = el("button", "summary-card"); summary.type = "button"; summary.style.setProperty("--resource-color", COLORS[id]); summary.setAttribute("aria-label", `Xem hiệu suất ${RESOURCE_NAMES[id]}`);
  const heading = el("div", "summary-card-heading"); const iconBox = el("span", "resource-icon"); iconBox.append(icon(id)); heading.append(el("span", "", RESOURCE_NAMES[id]), iconBox);
  const value = el("div", "summary-card-value", "—"); const subtitle = el("div", "summary-card-subtitle", "Đang chờ dữ liệu"); const sparkHost = el("div", "summary-card-spark"); const bar = meter(null);
  summary.append(heading, value, subtitle, sparkHost, bar); $("summary-grid").append(summary); const spark = new LiveChart(sparkHost, { compact: true }); summaryCards[id] = { element: summary, value, subtitle, bar: bar.firstChild, spark };
  summary.addEventListener("click", () => { selectResource(id); setPage("performance"); });
  const button = el("button", `resource-button${id === "cpu" ? " selected" : ""}`); button.type = "button"; button.id = `resource-${id}`; button.dataset.resource = id; button.style.setProperty("--resource-color", COLORS[id]); button.setAttribute("role", "tab"); button.setAttribute("aria-controls", "resource-detail"); button.setAttribute("aria-selected", String(id === "cpu")); button.tabIndex = id === "cpu" ? 0 : -1;
  const top = el("div", "resource-button-heading"); top.append(el("span", "", RESOURCE_NAMES[id]), icon(id)); const sub = el("span", "resource-button-subtitle", "Đang chờ dữ liệu"); const bottom = el("div", "resource-button-bottom"); const val = el("span", "resource-button-value", "—"); const sparkContainer = el("div", "resource-spark"); bottom.append(val, sparkContainer); button.append(top, sub, bottom); $("resource-selector").append(button); resourceButtons[id] = { element: button, value: val, subtitle: sub, spark: new LiveChart(sparkContainer, { compact: true }) };
  button.addEventListener("click", () => selectResource(id));
  button.addEventListener("keydown", (event) => { let index = RESOURCE_IDS.indexOf(id); if (["ArrowDown", "ArrowRight"].includes(event.key)) index = (index + 1) % RESOURCE_IDS.length; else if (["ArrowUp", "ArrowLeft"].includes(event.key)) index = (index + RESOURCE_IDS.length - 1) % RESOURCE_IDS.length; else if (event.key === "Home") index = 0; else if (event.key === "End") index = RESOURCE_IDS.length - 1; else return; event.preventDefault(); selectResource(RESOURCE_IDS[index]); resourceButtons[RESOURCE_IDS[index]].element.focus(); });
});
document.querySelectorAll("[data-icon]").forEach((node) => node.append(icon(node.dataset.icon)));

let latestSnapshot = null, displayedSnapshot = null, history = [], displayedHistory = [], activePage = "dashboard", selectedResource = "cpu", paused = false, lastReceived = 0, connected = false, source = null;
function setPage(page) { activePage = page; $("dashboard-page").hidden = page !== "dashboard"; $("performance-page").hidden = page !== "performance"; document.querySelectorAll(".nav-button").forEach((button) => { const active = button.dataset.page === page; button.classList.toggle("active", active); if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current"); }); setText("breadcrumb-current", page === "dashboard" ? "Dashboard" : "Performance"); document.title = `${page === "dashboard" ? "Dashboard" : "Performance"} · Server Monitor`; if (displayedSnapshot) renderVisible(); }
document.querySelectorAll(".nav-button").forEach((button) => button.addEventListener("click", () => setPage(button.dataset.page)));
function selectResource(id) { selectedResource = id; RESOURCE_IDS.forEach((key) => { const button = resourceButtons[key].element; const active = key === id; button.classList.toggle("selected", active); button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1; }); $("resource-detail").setAttribute("aria-labelledby", `resource-${id}`); if (displayedSnapshot) renderPerformance(displayedSnapshot); }
function rootDisk(snapshot) { return snapshot.disks?.find((disk) => disk.mountpoint === "/") || snapshot.disks?.[0] || null; }
function resourceValues(s) {
  const disk = rootDisk(s), gpu = s.gpus?.[0], diskBusy = s.disk_io?.busy_percent;
  return {
    cpu: { value: percent(s.cpu?.percent), percent: s.cpu?.percent, subtitle: `${number(s.cpu?.logical_cores)} luồng · ${frequency(s.cpu?.frequency_mhz)}`, short: s.cpu?.model || "Bộ xử lý", key: "cpu", max: 100 },
    memory: { value: percent(s.memory?.percent), percent: s.memory?.percent, subtitle: `${bytes(s.memory?.used_bytes)} / ${bytes(s.memory?.total_bytes)}`, short: `${bytes(s.memory?.used_bytes)} / ${bytes(s.memory?.total_bytes)}`, key: "memory", max: 100 },
    gpu: { value: gpu ? percent(gpu.utilization_percent) : "Không khả dụng", percent: gpu?.utilization_percent, subtitle: gpu ? gpu.name : "Không có dữ liệu GPU", short: gpu?.name || "Chưa có dữ liệu thiết bị GPU", key: "gpu", max: 100, unavailable: !gpu },
    disk: { value: disk ? percent(disk.percent) : "—", percent: disk?.percent, subtitle: disk ? `${bytes(disk.used_bytes)} / ${bytes(disk.total_bytes)}` : "Không có phân vùng", short: diskBusy !== null && valid(diskBusy) ? "Mức hoạt động ổ đĩa" : "Tốc độ đọc / ghi", key: valid(diskBusy) ? "disk" : "disk_read", max: valid(diskBusy) ? 100 : dynamicMax(displayedHistory, ["disk_read", "disk_write"]) },
    network: { value: rate(s.network?.recv_bytes_per_sec), percent: null, subtitle: `Gửi ${rate(s.network?.sent_bytes_per_sec)}`, short: "Tổng lưu lượng mạng", key: "network_recv", max: dynamicMax(displayedHistory, ["network_recv", "network_sent"]) }
  };
}
function dynamicMax(data, keys) { const end = data.length ? data[data.length - 1].timestamp : 0; const values = data.filter((point) => point.timestamp >= end - 60).flatMap((point) => keys.map((key) => point[key]).filter(valid)); const peak = values.length ? Math.max(...values) : 0; if (peak <= 1024) return 1024; const exponent = Math.pow(2, Math.ceil(Math.log2(peak * 1.15))); return exponent; }
function renderResourceCards(s) { const values = resourceValues(s); RESOURCE_IDS.forEach((id) => { const info = values[id]; const card = summaryCards[id]; card.value.textContent = info.value; card.subtitle.textContent = info.subtitle; card.element.classList.toggle("unavailable", !!info.unavailable); card.bar.style.width = `${valid(info.percent) ? Math.max(0, Math.min(100, info.percent)) : 0}%`; card.bar.parentElement.hidden = id === "network"; const series = [{ key: info.key, color: COLORS[id], name: RESOURCE_NAMES[id] }]; card.spark.update(displayedHistory, series, { max: info.max }); const button = resourceButtons[id]; button.value.textContent = id === "disk" ? valid(s.disk_io?.busy_percent) ? percent(s.disk_io.busy_percent) : rate(s.disk_io?.read_bytes_per_sec) : info.value; button.value.classList.toggle("unavailable", !!info.unavailable); button.subtitle.textContent = info.short; button.subtitle.title = info.short; button.spark.update(displayedHistory, series, { max: info.max }); }); }
function renderVisible() { if (!displayedSnapshot) return; const s = displayedSnapshot; renderResourceCards(s); setText("dashboard-updated", time(s.timestamp)); setText("performance-updated", time(s.timestamp)); const warnings = (s.warnings || []).filter((value) => typeof value === "string"); $("collection-notes").hidden = !warnings.length; setText("collection-note-count", warnings.length ? `(${number(warnings.length)})` : ""); $("collection-note-list").replaceChildren(...warnings.map((warning) => el("li", "", warning))); if (activePage === "dashboard") renderDashboard(s); else renderPerformance(s); }
function renderDashboard(s) {
  setText("server-hostname", s.system?.hostname || "Máy chủ"); setText("server-os", s.system?.os || "Hệ điều hành chưa xác định"); setText("server-ip", mainAddress(s)); setText("server-uptime", uptime(s.system?.uptime_seconds));
  overviewChart.update(displayedHistory, [{ key: "cpu", color: COLORS.cpu, name: "CPU" }, { key: "memory", color: COLORS.memory, name: "Bộ nhớ" }]);
  const info = [ ["Bộ xử lý", s.cpu?.model || "—"], ["Lõi / luồng", `${number(s.cpu?.physical_cores)} lõi / ${number(s.cpu?.logical_cores)} luồng`], ["Tần số CPU", frequency(s.cpu?.frequency_mhz)], ["Tổng bộ nhớ", bytes(s.memory?.total_bytes)], ["Kernel", s.system?.kernel || "—"], ["Kiến trúc", s.system?.architecture || "—"], ["Nhiệt độ CPU", temperature(s.cpu?.temperature_celsius)], ["Khởi động lúc", dateTime(s.system?.boot_time)] ];
  $("system-details").replaceChildren(...info.map(([label, value]) => labelValue(label, value, "system-detail-row")));
  renderDisks($("storage-list"), s.disks); setText("network-recv", rate(s.network?.recv_bytes_per_sec)); setText("network-sent", rate(s.network?.sent_bytes_per_sec));
  networkChart.update(displayedHistory, [{ key: "network_recv", color: COLORS.network, name: "Nhận" }, { key: "network_sent", color: COLORS.teal, name: "Gửi" }], { max: dynamicMax(displayedHistory, ["network_recv", "network_sent"]), formatter: rate, axisFormatter: (value) => bytes(value, 0) });
  const interfaces = s.network?.interfaces || []; $("network-interface-list").replaceChildren(...interfaces.slice(0, 4).map((iface) => { const row = el("div", "interface-row"); row.append(el("span", "interface-name", iface.name), el("span", "interface-address", iface.address || "Không có địa chỉ IP"), interfaceStatus(iface.is_up)); return row; }));
  const sampledAt = s.processes?.sampled_at; const freshness = valid(sampledAt) ? ` · cập nhật ${time(sampledAt)}` : s.processes && "sampled_at" in s.processes ? " · đang thu thập chi tiết" : "";
  setText("processes-summary", `${number(s.processes?.total)} tiến trình · ${number(s.processes?.threads)} luồng${freshness} · CPU tiến trình có thể vượt 100% khi dùng nhiều lõi`);
  const processes = s.processes?.top || []; const rows = processes.slice(0, 8).map((process) => { const row = el("tr"); const name = el("td"); const nameWrap = el("span", "process-name"); nameWrap.append(el("i", "process-dot"), el("span", "", process.name || "Không rõ tên")); name.append(nameWrap); const status = el("td"); status.append(el("span", `process-status${process.status === "running" ? " running" : ""}`, process.status || "—")); row.append(name, el("td", "", number(process.pid)), el("td", "", percent(process.cpu_percent)), el("td", "", `${bytes(process.memory_bytes)} (${percent(process.memory_percent)})`), status); return row; });
  if (!rows.length) { const row = el("tr"); const cell = el("td", "empty-state", s.processes?.total ? "Đang thu thập dữ liệu chi tiết tiến trình…" : "Không có dữ liệu tiến trình"); cell.colSpan = 5; row.append(cell); rows.push(row); } $("process-table-body").replaceChildren(...rows);
}
function renderDisks(host, disks) { host.replaceChildren(...(disks || []).map((disk) => { const item = el("div", "storage-item"); const heading = el("div", "storage-item-heading"); heading.append(el("strong", "", disk.mountpoint || disk.device), el("span", "", percent(disk.percent))); const meta = el("div", "storage-item-meta"); meta.append(el("span", "", `${disk.device || ""} · ${disk.filesystem || ""}`), el("span", "", `${bytes(disk.used_bytes)} / ${bytes(disk.total_bytes)}`)); item.append(heading, meta, meter(disk.percent, COLORS.disk)); return item; })); if (!disks?.length) host.append(el("p", "empty-state", "Không có thông tin phân vùng")); }
function interfaceStatus(up) { const status = el("span", `interface-status${up ? "" : " down"}`); status.append(el("i", "status-dot"), el("span", "", up ? "Đang hoạt động" : "Đã ngắt")); return status; }
function renderInterfaces(host, interfaces) { host.replaceChildren(...(interfaces || []).map((iface) => { const item = el("div", "interface-detail"); const head = el("div", "interface-detail-head"); head.append(el("strong", "", iface.name), interfaceStatus(iface.is_up), el("span", "interface-detail-meta", `${iface.address || "Không có IP"}${valid(iface.speed_mbps) && iface.speed_mbps > 0 ? ` · ${number(iface.speed_mbps)} Mbps` : ""}`)); const values = el("div", "interface-detail-values"); [["Nhận", rate(iface.recv_bytes_per_sec)], ["Gửi", rate(iface.sent_bytes_per_sec)], ["Tổng đã nhận", bytes(iface.recv_bytes)], ["Tổng đã gửi", bytes(iface.sent_bytes)]].forEach(([label, value]) => { const field = el("div", "", label); field.append(el("strong", "", value)); values.append(field); }); item.append(head, values); return item; })); if (!interfaces?.length) host.append(el("p", "empty-state", "Không có thông tin giao diện mạng")); }
function detailStats(stats) { $("detail-stats").replaceChildren(...stats.map(([label, value]) => { const row = labelValue(label, value, "detail-stat"); if (String(value).length > 25) row.lastChild.classList.add("small-value"); return row; })); }
function legend(series) { $("detail-chart-legend").replaceChildren(...series.map((s) => { const item = el("span"); const dot = el("i", "legend-dot"); dot.style.background = s.color; item.append(dot, el("span", "", s.name)); return item; })); }
function renderPerformance(s) {
  const id = selectedResource, gpu = s.gpus?.[0], io = s.disk_io || {}, net = s.network || {}, cpu = s.cpu || {}, memory = s.memory || {};
  $("resource-detail").style.setProperty("--resource-color", COLORS[id]); $("resource-current").parentElement.classList.toggle("unavailable", id === "gpu" && !gpu); setText("resource-title", RESOURCE_NAMES[id]); $("resource-extra-icon").replaceChildren(icon(id));
  let eyebrow, subtitle, current, currentLabel, unit = "% sử dụng", series = [{ key: id, name: RESOURCE_NAMES[id], color: COLORS[id] }], max = 100, formatter = (value) => percent(value), stats = [], extraTitle, extraSubtitle;
  const extra = $("resource-extra"); extra.replaceChildren();
  if (id === "cpu") {
    eyebrow = "PROCESSOR"; subtitle = cpu.model || "Bộ xử lý hệ thống"; current = percent(cpu.percent); currentLabel = "Mức sử dụng CPU";
    stats = [["Tần số hiện tại", frequency(cpu.frequency_mhz)], ["Lõi / luồng", `${number(cpu.physical_cores)} / ${number(cpu.logical_cores)}`], ["Tiến trình", number(s.processes?.total)], ["Luồng tiến trình", number(s.processes?.threads)], ["Thời gian hoạt động", uptime(s.system?.uptime_seconds)], ["Nhiệt độ CPU", temperature(cpu.temperature_celsius)], ["Load average 1 / 5 / 15 phút", cpu.load_average ? cpu.load_average.map((v) => number(v, 2)).join(" / ") : "Không hỗ trợ"], ["Kiến trúc", s.system?.architecture || "—"]];
    extraTitle = "Hoạt động từng lõi"; extraSubtitle = `${number(cpu.logical_cores)} bộ xử lý logic · mức sử dụng độc lập`;
    const grid = el("div", "core-grid"); (cpu.per_core || []).forEach((value, index) => { const item = el("div", "core-item"); const heading = el("div", "core-item-heading"); heading.append(el("span", "", `CPU ${index}`), el("strong", "", percent(value, 0))); item.append(heading, meter(value, COLORS.cpu)); grid.append(item); }); extra.append(grid); if (!cpu.per_core?.length) extra.append(el("p", "empty-state", "Chưa có dữ liệu từng lõi CPU"));
    if (s.sensors?.length) { const sensorWrap = el("div", "sensor-readings"); sensorWrap.append(el("h3", "", "Cảm biến nhiệt độ")); const sensorList = el("div", "sensor-grid"); s.sensors.filter((sensor) => valid(sensor.current)).forEach((sensor) => { const row = el("div", "sensor-reading"); row.append(el("span", "", sensor.label || "Cảm biến"), el("strong", "", temperature(sensor.current))); if (valid(sensor.critical) || valid(sensor.high)) row.title = `${valid(sensor.high) ? `Ngưỡng cao: ${temperature(sensor.high)}` : ""}${valid(sensor.critical) ? ` · Ngưỡng tới hạn: ${temperature(sensor.critical)}` : ""}`; if (valid(sensor.high) && sensor.current >= sensor.high || valid(sensor.critical) && sensor.current >= sensor.critical) row.classList.add("sensor-hot"); sensorList.append(row); }); sensorWrap.append(sensorList); extra.append(sensorWrap); }
  } else if (id === "memory") {
    eyebrow = "SYSTEM MEMORY"; subtitle = `${bytes(memory.total_bytes)} RAM · bộ nhớ vật lý`; current = percent(memory.percent); currentLabel = "Mức sử dụng bộ nhớ";
    stats = [["Đang sử dụng", bytes(memory.used_bytes)], ["Khả dụng", bytes(memory.available_bytes)], ["Tổng bộ nhớ", bytes(memory.total_bytes)], ["Cache", bytes(memory.cached_bytes)], ["Buffers", bytes(memory.buffers_bytes)], ["Swap đã dùng", bytes(memory.swap?.used_bytes)], ["Tổng swap", bytes(memory.swap?.total_bytes)], ["Mức sử dụng swap", percent(memory.swap?.percent)]];
    extraTitle = "Phân bổ bộ nhớ"; extraSubtitle = "Bộ nhớ khả dụng bao gồm cache có thể thu hồi";
    const wrap = el("div", "memory-breakdown"), segments = el("div", "memory-segments"); const used = valid(memory.total_bytes) && memory.total_bytes > 0 && valid(memory.available_bytes) ? Math.max(0, Math.min(100, (memory.total_bytes - memory.available_bytes) / memory.total_bytes * 100)) : valid(memory.percent) ? memory.percent : 0;
    const usedSegment = el("span", "memory-segment-used"); usedSegment.style.width = `${used}%`; const availableSegment = el("span", "memory-segment-available"); availableSegment.style.width = `${100 - used}%`; segments.append(usedSegment, availableSegment); const breakdownLegend = el("div", "memory-breakdown-legend"); [["Đang chiếm dụng", valid(memory.total_bytes) && valid(memory.available_bytes) ? bytes(memory.total_bytes - memory.available_bytes) : "—", COLORS.memory], ["Khả dụng", bytes(memory.available_bytes), "#607793"]].forEach(([label, value, color]) => { const row = el("div"), dot = el("i", "legend-dot"); dot.style.background = color; row.append(dot, el("span", "", label), el("strong", "", value)); breakdownLegend.append(row); }); wrap.append(segments, breakdownLegend, el("p", "muted-caption", "Giá trị đang sử dụng và khả dụng do hệ điều hành cung cấp; cache và buffers được hiển thị riêng ở trên.")); extra.append(wrap);
  } else if (id === "gpu") {
    eyebrow = "GRAPHICS PROCESSOR"; subtitle = gpu?.name || "Chưa có dữ liệu GPU trên máy chủ này"; current = gpu ? percent(gpu.utilization_percent) : "Không khả dụng"; currentLabel = gpu ? "Mức sử dụng GPU đầu tiên" : "Không có dữ liệu";
    stats = gpu ? [["Bộ nhớ GPU đã dùng", bytes(gpu.memory_used_bytes)], ["Tổng bộ nhớ GPU", bytes(gpu.memory_total_bytes)], ["Mức sử dụng bộ nhớ", percent(gpu.memory_percent)], ["Nhiệt độ", temperature(gpu.temperature_celsius)], ["Công suất", valid(gpu.power_watts) ? `${number(gpu.power_watts, 1)} W` : "Không có dữ liệu"], ["GPU phát hiện", number(s.gpus.length)]] : [["Trạng thái", s.gpu_status || "Không có GPU hoặc công cụ thu thập phù hợp"], ["GPU phát hiện", "0"]];
    extraTitle = "Thiết bị GPU"; extraSubtitle = "Dữ liệu GPU được cung cấp khi máy chủ hỗ trợ";
    if (gpu) { const wrap = el("div", "gpu-extra"); s.gpus.forEach((device) => { const card = el("div", "gpu-device"), heading = el("div", "gpu-device-name"), fields = el("div", "gpu-device-stats"); heading.append(el("span", "", `${device.index === undefined ? "GPU" : `GPU ${device.index}`} · ${device.name}`), el("span", "", percent(device.utilization_percent))); [["Bộ nhớ", `${bytes(device.memory_used_bytes)} / ${bytes(device.memory_total_bytes)}`], ["Nhiệt độ", temperature(device.temperature_celsius)], ["Công suất", valid(device.power_watts) ? `${number(device.power_watts, 1)} W` : "—"]].forEach(([label, value]) => { const field = el("span", "", label); field.append(el("strong", "", value)); fields.append(field); }); card.append(heading, fields); wrap.append(card); }); extra.append(wrap); }
    else { const message = el("div", "resource-empty"), text = el("div"); text.append(el("strong", "", "Chưa có dữ liệu GPU"), el("p", "", s.gpu_status || "Máy chủ chưa phát hiện GPU được hỗ trợ. Dashboard sẽ tự hiển thị khi có dữ liệu thiết bị.")); message.append(icon("gpu"), text); extra.append(message); }
  } else if (id === "disk") {
    const busy = valid(io.busy_percent); eyebrow = "DISK ACTIVITY"; subtitle = "Hoạt động I/O tổng hợp trên máy chủ"; current = busy ? percent(io.busy_percent) : rate(io.read_bytes_per_sec); currentLabel = busy ? "Mức hoạt động ổ đĩa" : "Tốc độ đọc hiện tại";
    if (!busy) { unit = "Tốc độ đọc / ghi (byte mỗi giây)"; series = [{ key: "disk_read", color: COLORS.disk, name: "Đọc" }, { key: "disk_write", color: COLORS.memory, name: "Ghi" }]; max = dynamicMax(displayedHistory, ["disk_read", "disk_write"]); formatter = rate; }
    const disk = rootDisk(s); stats = [["Tốc độ đọc", rate(io.read_bytes_per_sec)], ["Tốc độ ghi", rate(io.write_bytes_per_sec)], ["Tổng đã đọc", bytes(io.read_bytes)], ["Tổng đã ghi", bytes(io.write_bytes)], ["Phân vùng hệ thống", disk?.mountpoint || "—"], ["Dung lượng đã dùng", bytes(disk?.used_bytes)], ["Dung lượng còn trống", bytes(disk?.free_bytes)], ["Phân vùng", number(s.disks?.length)]];
    extraTitle = "Chi tiết phân vùng"; extraSubtitle = "Dung lượng thực tế của từng filesystem"; const list = el("div", "detail-storage-list"); renderDisks(list, s.disks); extra.append(list);
  } else {
    eyebrow = "NETWORK TRAFFIC"; subtitle = "Lưu lượng tổng hợp · các giao diện mạng hoạt động"; current = rate(net.recv_bytes_per_sec); currentLabel = "Tốc độ nhận hiện tại"; unit = "Lưu lượng (byte mỗi giây)"; series = [{ key: "network_recv", color: COLORS.network, name: "Nhận" }, { key: "network_sent", color: COLORS.teal, name: "Gửi" }]; max = dynamicMax(displayedHistory, ["network_recv", "network_sent"]); formatter = rate;
    stats = [["Tốc độ nhận", rate(net.recv_bytes_per_sec)], ["Tốc độ gửi", rate(net.sent_bytes_per_sec)], ["Tổng đã nhận", bytes(net.recv_bytes)], ["Tổng đã gửi", bytes(net.sent_bytes)], ["Giao diện", number(net.interfaces?.length)], ["Đang hoạt động", number(net.interfaces?.filter((iface) => iface.is_up).length)], ["Địa chỉ máy chủ", mainAddress(s)], ["Hostname", s.system?.hostname || "—"]];
    extraTitle = "Giao diện mạng"; extraSubtitle = "Tốc độ, địa chỉ và lưu lượng theo từng giao diện"; const list = el("div", "detail-interface-list"); renderInterfaces(list, net.interfaces); extra.append(list);
  }
  setText("resource-eyebrow", eyebrow); setText("resource-subtitle", subtitle); setText("resource-current", current); setText("resource-current-label", currentLabel); setText("detail-chart-unit", unit); setText("resource-extra-title", extraTitle); setText("resource-extra-subtitle", extraSubtitle); detailStats(stats); legend(series.length > 1 ? series : []); detailChart.update(displayedHistory, series, { max, formatter, axisFormatter: formatter === rate ? (value) => bytes(value, 0) : (value) => percent(value, 0), unavailable: id === "gpu" ? "Chưa có dữ liệu GPU trên máy chủ" : "Đang chờ mẫu dữ liệu đầu tiên…" });
}
function mainAddress(s) {
  const interfaces = s.network?.interfaces || [], addresses = s.system?.addresses || [];
  const nonLoopback = (address) => typeof address === "string" && address && !address.startsWith("127.") && address !== "::1" && address !== "0.0.0.0";
  const routableV4 = (address) => nonLoopback(address) && /^\d+\.\d+\.\d+\.\d+$/.test(address) && !address.startsWith("169.254.");
  const nonLoopbackV6 = (address) => nonLoopback(address) && address.includes(":");
  const active = interfaces.filter((iface) => iface.is_up).flatMap((iface) => [iface.address, ...addresses.filter((entry) => entry.interface === iface.name).map((entry) => entry.address)]).filter(nonLoopback);
  const all = addresses.map((entry) => entry.address).filter(nonLoopback);
  return active.find(routableV4) || active.find(nonLoopbackV6) || all.find(routableV4) || all.find(nonLoopbackV6) || active[0] || "Không có IP";
}
function historyPoint(s) { return { timestamp: s.timestamp, cpu: s.cpu?.percent ?? null, memory: s.memory?.percent ?? null, gpu: s.gpus?.[0]?.utilization_percent ?? null, disk: s.disk_io?.busy_percent ?? null, network_recv: s.network?.recv_bytes_per_sec ?? null, network_sent: s.network?.sent_bytes_per_sec ?? null, disk_read: s.disk_io?.read_bytes_per_sec ?? null, disk_write: s.disk_io?.write_bytes_per_sec ?? null }; }
function ingest(payload) {
  const snapshot = payload?.snapshot || payload; if (!snapshot || !valid(snapshot.timestamp) || !snapshot.system || !snapshot.cpu) return;
  const incoming = Array.isArray(payload.history) ? payload.history : [];
  const merged = new Map(history.map((point) => [point.timestamp, point])); incoming.filter((point) => valid(point?.timestamp)).forEach((point) => merged.set(point.timestamp, point)); merged.set(snapshot.timestamp, historyPoint(snapshot));
  const end = Math.max(snapshot.timestamp, latestSnapshot?.timestamp || snapshot.timestamp); history = [...merged.values()].filter((point) => point.timestamp >= end - 180).sort((a, b) => a.timestamp - b.timestamp);
  if (latestSnapshot && snapshot.timestamp < latestSnapshot.timestamp) return;
  latestSnapshot = snapshot; lastReceived = Date.now(); connected = true; updateConnection(); setText("sidebar-host", snapshot.system.hostname || "Máy chủ"); setText("sidebar-address", mainAddress(snapshot));
  if (!paused) { displayedSnapshot = snapshot; displayedHistory = history.slice(); renderVisible(); }
}
function updateConnection() {
  const stale = lastReceived && Date.now() - lastReceived > 5500; const online = connected && lastReceived > 0 && !stale; const state = $("connection-state"); state.className = `connection-state${online ? " connected" : " reconnecting"}`;
  setText("connection-text", online ? paused ? "Đã tạm dừng hiển thị" : "Đang cập nhật · 1 giây" : latestSnapshot ? "Đang kết nối lại" : "Đang kết nối"); $("sidebar-status").classList.toggle("online", !!online); setText("server-online-text", online ? "Đang hoạt động" : "Chờ kết nối");
  const notice = $("connection-notice"); notice.hidden = online && !paused; notice.textContent = paused ? "Đã tạm dừng hiển thị. Máy chủ vẫn thu thập dữ liệu; tiếp tục để xem số liệu mới nhất." : latestSnapshot ? "Mất kết nối với máy chủ. Đang tự động kết nối lại; số liệu đang hiển thị là lần cập nhật gần nhất." : "Đang kết nối tới máy chủ và lấy số liệu tài nguyên…";
  document.querySelectorAll(".live-badge").forEach((badge) => { badge.lastChild.textContent = paused ? "PAUSED" : online ? "LIVE" : "WAITING"; });
}
$("pause-button").addEventListener("click", () => { paused = !paused; const button = $("pause-button"); const title = paused ? "Tiếp tục hiển thị" : "Tạm dừng hiển thị"; button.classList.toggle("paused", paused); button.setAttribute("aria-label", title); button.title = title; button.replaceChildren(icon(paused ? "play" : "pause"), el("span", "", paused ? "Tiếp tục" : "Tạm dừng")); if (!paused && latestSnapshot) { displayedSnapshot = latestSnapshot; displayedHistory = history.slice(); renderVisible(); } updateConnection(); });
async function fetchSnapshot() { try { const response = await fetch("/api/snapshot", { cache: "no-store" }); if (!response.ok) throw new Error(`HTTP ${response.status}`); ingest(await response.json()); } catch { connected = false; updateConnection(); } }
function connect() {
  source = new EventSource("/api/events"); const receive = (event) => { try { ingest(JSON.parse(event.data)); } catch { /* Keep the last valid server sample. */ } };
  source.onmessage = receive; source.addEventListener("snapshot", receive); source.onopen = () => { connected = true; updateConnection(); }; source.onerror = () => { connected = false; updateConnection(); };
}
updateConnection(); fetchSnapshot(); connect(); setInterval(updateConnection, 1500);
window.addEventListener("online", () => { fetchSnapshot(); if (source?.readyState === EventSource.CLOSED) connect(); });
window.addEventListener("offline", () => { connected = false; updateConnection(); });
window.addEventListener("pageshow", (event) => { if (event.persisted) { fetchSnapshot(); if (source?.readyState === EventSource.CLOSED) connect(); } });
window.addEventListener("resize", () => { if (displayedSnapshot) renderVisible(); });
