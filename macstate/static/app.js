/* MacState admin UI: hash-routed single page, no build step. */
"use strict";

// ---- helpers ---------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);
let ME = null;
let refreshTimer = null;

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Please sign in"); }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    let msg = data?.detail ?? data;
    if (Array.isArray(msg)) msg = msg.map(e => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), bad ? 6000 : 3000);
}

async function act(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg);
    return r;
  } catch (e) { toast(e.message, true); throw e; }
}

function fmtTime(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
function bytes(n) {
  n = Number(n);
  if (!n) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

const STATUS_CLASS = {
  succeeded: "ok", compliant: "ok", installed: "ok", updated: "ok", uninstalled: "ok", remediated: "ok",
  failed: "bad", noncompliant: "bad", cancelled: "warn", skipped: "warn",
  running: "info", pending: "",
};
const badge = (s, label) => h("span", { class: `badge ${STATUS_CLASS[s] ?? ""}` }, label ?? s ?? "—");
const DESIRED_LABEL = {
  installed: "Installed (any version)", latest: "Latest version", version: "Specific version",
  uninstalled: "Uninstalled", ignore: "Ignore", enforce: "Enforce", monitor: "Monitor only",
};

function table(cols, rows, onClick) {
  if (!rows.length) return h("div", { class: "empty" }, "Nothing here yet.");
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, cols.map(c => h("th", {}, c[0])))),
    h("tbody", {}, rows.map(r => h("tr", { class: onClick ? "click" : "", onclick: onClick ? (e) => {
      if (e.target.closest("button,a,input,select")) return;
      onClick(r);
    } : null }, cols.map(c => h("td", {}, c[1](r))))))));
}

function pageHead(title, ...buttons) {
  return h("div", { class: "page-head" }, h("h1", {}, title), h("div", { class: "actions" }, buttons));
}

function btn(label, onclick, cls = "") { return h("button", { class: `btn ${cls}`, type: "button", onclick }, label); }

function copyBox(text) {
  return h("div", { class: "copy" }, h("code", {}, text),
    btn("Copy", () => navigator.clipboard.writeText(text).then(() => toast("Copied"))));
}

// ---- modal forms -----------------------------------------------------------

function closeModal() { $("#modal").classList.add("hidden"); $("#modal-box").replaceChildren(); }
$("#modal").addEventListener("mousedown", e => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

function openModal(title, body, footer) {
  $("#modal-box").replaceChildren(h("h2", {}, title), body, footer || "");
  $("#modal").classList.remove("hidden");
}

/* fields: [{name,label,type,options,hint,show(values),required,placeholder}] */
function formModal(title, fields, values, onSubmit, submitLabel = "Save", onChange = null) {
  values = { ...values };
  const inputs = {};
  const wrap = h("div", { class: "form" });
  const err = h("div", { class: "error" });

  function current() {
    const v = { ...values };
    for (const f of fields) {
      const el = inputs[f.name];
      if (!el) continue;
      if (f.type === "checkbox") v[f.name] = el.checked;
      else if (f.type === "number") v[f.name] = el.value === "" ? null : Number(el.value);
      else if (f.type === "params") v[f.name] = parseParams(el.value);
      else v[f.name] = el.value;
    }
    return v;
  }
  function render() {
    const v = current();
    Object.assign(values, v);
    wrap.replaceChildren();
    for (const k of Object.keys(inputs)) delete inputs[k];
    for (const f of fields) {
      if (f.show && !f.show(values)) continue;
      let el;
      const val = values[f.name] ?? f.default ?? "";
      if (f.type === "select") {
        const opts = typeof f.options === "function" ? f.options(values) : f.options;
        el = h("select", { name: f.name, onchange: async () => {
          render();
          if (onChange) { await onChange(values); render(); }
        } },
          opts.map(([ov, ol]) => h("option", { value: ov, selected: String(ov) === String(val) }, ol)));
      } else if (f.type === "textarea" || f.type === "code" || f.type === "params") {
        const text = f.type === "params" ? formatParams(val || {}) : val;
        el = h("textarea", { name: f.name, class: f.type === "textarea" ? "" : "code", spellcheck: "false",
          placeholder: f.placeholder || "" }, text);
        el.value = text;
      } else if (f.type === "checkbox") {
        el = h("input", { type: "checkbox", name: f.name });
        el.checked = !!val;
        inputs[f.name] = el;
        wrap.append(h("label", { class: "check" }, el, f.label));
        if (f.hint) wrap.append(h("div", { class: "hint" }, f.hint));
        continue;
      } else {
        el = h("input", { name: f.name, type: f.type || "text", value: val, placeholder: f.placeholder || "",
          required: f.required });
      }
      inputs[f.name] = el;
      wrap.append(h("label", {}, f.label, el));
      if (f.hint) wrap.append(h("div", { class: "hint" }, f.hint));
    }
  }
  render();
  const save = h("button", { class: "btn primary", type: "submit" }, submitLabel);
  const form = h("form", { onsubmit: async e => {
    e.preventDefault();
    save.disabled = true;
    err.textContent = "";
    try { await onSubmit(current()); closeModal(); }
    catch (ex) { err.textContent = ex.message; }
    finally { save.disabled = false; }
  } }, wrap, err, h("div", { class: "modal-foot" }, btn("Cancel", closeModal), save));
  openModal(title, form);
  setTimeout(() => wrap.querySelector("input,select,textarea")?.focus(), 30);
}

function parseParams(text) {
  const out = {};
  for (const line of text.split("\n")) {
    const i = line.indexOf("=");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}
const formatParams = obj => Object.entries(obj).map(([k, v]) => `${k}=${v}`).join("\n");

function confirmModal(title, message, onYes, yesLabel = "Delete") {
  openModal(title, h("p", {}, message), h("div", { class: "modal-foot" },
    btn("Cancel", closeModal),
    btn(yesLabel, async () => { try { await onYes(); closeModal(); } catch (e) { toast(e.message, true); } }, "primary")));
}

// ---- lookups ---------------------------------------------------------------

async function targetOptions() {
  const [tenants, tags, computers] = await Promise.all([api("GET", "/api/tenants"), api("GET", "/api/tags"),
    api("GET", "/api/computers")]);
  return {
    types: [["all", "All computers"], ["tenant", "Tenant"], ["tag", "Tag"], ["computer", "Computer"]],
    ids: v => ({
      tenant: tenants.map(t => [t.id, t.name]),
      tag: tags.map(t => [t.id, t.name]),
      computer: computers.map(c => [c.id, `${c.name} (${c.tenant_name})`]),
    })[v.target_type] || [],
  };
}

// ---- pages -----------------------------------------------------------------

const routes = {
  "": pageDashboard,
  computers: pageComputers,
  tenants: pageTenants,
  software: pageSoftware,
  tasks: pageTasks,
  deployments: pageDeployments,
  schedules: pageSchedules,
  sessions: pageSessions,
  settings: pageSettings,
  ninjaone: pageNinja,
};

async function route() {
  clearInterval(refreshTimer);
  const [section, id] = location.hash.replace(/^#\/?/, "").split("/");
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("active", a.dataset.nav === (section || "")));
  const main = $("#main");
  const fn = routes[section || ""];
  if (!fn) { main.replaceChildren(h("p", {}, "Not found")); return; }
  try {
    await fn(main, id ? decodeURIComponent(id) : null);
  } catch (e) {
    if (e.message !== "Please sign in") main.replaceChildren(h("div", { class: "card error" }, e.message));
  }
}

function autoRefresh(fn, ms = 10000) {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => { if ($("#modal").classList.contains("hidden")) fn().catch(() => {}); }, ms);
}

// Dashboard ------------------------------------------------------------------

async function pageDashboard(main) {
  const render = async () => {
    const d = await api("GET", "/api/dashboard");
    const s24 = d.sessions_24h;
    main.replaceChildren(
      pageHead("Dashboard", btn("Run maintenance…", bulkMaintenance, "primary")),
      h("div", { class: "stats" },
        stat(d.computers, "Computers"), stat(d.online, "Online now"),
        stat(d.noncompliant_computers, "Non-compliant computers"), stat(d.active_sessions, "Active sessions"),
        stat(s24.succeeded || 0, "Sessions OK (24h)"), stat(s24.failed || 0, "Sessions failed (24h)")),
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", {}, "Non-compliant items"),
          table([
            ["Computer", r => h("a", { href: `#/computers/${r.computer_id}` }, r.computer_name)],
            ["Item", r => r.name],
            ["Detected", r => r.detected || "—"],
            ["Status", r => badge(r.status)],
          ], d.noncompliant_items)),
        h("div", { class: "card" }, h("h2", {}, "Recent sessions"),
          table([
            ["#", r => h("a", { href: `#/sessions/${r.id}` }, r.id)],
            ["Computer", r => r.computer_name],
            ["Kind", r => `${r.kind} · ${r.mode}`],
            ["Status", r => badge(r.status)],
            ["Created", r => ago(r.created_at)],
          ], d.recent_sessions, r => location.hash = `#/sessions/${r.id}`))),
      !d.agent.version ? h("div", { class: "card" }, h("h2", {}, "Agent not built"),
        h("p", {}, "Run ", h("code", {}, "./build-agent.sh"), " on the server so Macs can download the agent.")) : "",
    );
  };
  await render();
  autoRefresh(render);
}
const stat = (v, l) => h("div", { class: "stat" }, h("div", { class: "v" }, v ?? 0), h("div", { class: "l" }, l));

async function bulkMaintenance() {
  const t = await targetOptions();
  formModal("Run maintenance", [
    { name: "target_type", label: "Target", type: "select", options: t.types },
    { name: "target_id", label: "Which", type: "select", options: t.ids, show: v => v.target_type !== "all" },
    { name: "mode", label: "Mode", type: "select", options: [["apply", "Apply (install / remediate)"], ["detect", "Detect only"]] },
  ], { target_type: "all", mode: "apply" }, async v => {
    const r = await api("POST", "/api/maintenance", { ...v, target_id: v.target_id ? Number(v.target_id) : null });
    toast(`Queued ${r.session_ids.length} session(s)`);
  }, "Queue");
}

// Computers ------------------------------------------------------------------

async function pageComputers(main, id) {
  if (id) return pageComputer(main, Number(id));
  const tenants = await api("GET", "/api/tenants");
  let filter = "", tenant = "";
  const body = h("div", { class: "card" });
  const draw = async () => {
    const rows = await api("GET", "/api/computers" + (tenant ? `?tenant_id=${tenant}` : ""));
    const f = filter.toLowerCase();
    const shown = rows.filter(r => !f || [r.name, r.serial, r.model, r.console_user, r.tags].join(" ").toLowerCase().includes(f));
    body.replaceChildren(table([
      ["Name", r => h("span", {}, h("span", { class: `dot ${r.online ? "on" : ""}`, title: r.online ? "online" : "offline" }), r.name,
        r.ninja_device_id ? [" ", h("span", { class: "badge info", title: "Linked to NinjaOne" }, "N1")] : "")],
      ["Tenant", r => r.tenant_name],
      ["User", r => r.console_user || "—"],
      ["OS", r => r.os_version],
      ["Model", r => r.model],
      ["Tags", r => r.tags || ""],
      ["Compliance", r => r.noncompliant ? badge("failed", `${r.noncompliant} issue(s)`) : badge("compliant", "OK")],
      ["Last seen", r => ago(r.last_seen)],
    ], shown, r => location.hash = `#/computers/${r.id}`));
  };
  main.replaceChildren(
    pageHead("Computers", btn("Enroll a Mac", () => location.hash = "#/tenants"), btn("Run maintenance…", bulkMaintenance, "primary")),
    h("div", { class: "toolbar" },
      h("input", { placeholder: "Search name, serial, user, tag…", oninput: e => { filter = e.target.value; draw(); } }),
      h("select", { onchange: e => { tenant = e.target.value; draw(); } },
        h("option", { value: "" }, "All tenants"), tenants.map(t => h("option", { value: t.id }, t.name)))),
    body);
  await draw();
  autoRefresh(draw, 15000);
}

async function pageComputer(main, id) {
  let tab = "state";
  const render = async () => {
    const c = await api("GET", `/api/computers/${id}`);
    const inv = c.inventory || {};
    const tabs = h("div", { class: "tabs" }, [["state", "Desired state"], ["inventory", "Inventory"], ["apps", `Applications (${(inv.apps || []).length})`], ["sessions", "Sessions"]]
      .map(([k, l]) => h("button", { class: tab === k ? "active" : "", onclick: () => { tab = k; render(); } }, l)));
    let content;
    if (tab === "state") {
      content = h("div", { class: "card" }, table([
        ["Item", r => h("span", {}, r.name, " ", h("span", { class: "muted" }, r.item_type))],
        ["Desired", r => DESIRED_LABEL[r.desired] + (r.version ? ` ${r.version}` : "")],
        ["From", r => h("span", { class: "muted" }, r.source)],
        ["Detected", r => r.detected ?? h("span", { class: "muted" }, "not yet checked")],
        ["Status", r => r.status ? badge(r.compliant ? "compliant" : r.status, r.status) : badge(null, "unknown")],
        ["Checked", r => ago(r.checked_at)],
      ], c.desired));
    } else if (tab === "inventory") {
      const facts = [
        ["Serial", c.serial], ["Model", c.model], ["OS", `${c.os_version} ${inv.os_build ? `(${inv.os_build})` : ""}`],
        ["Architecture", c.arch], ["CPU", inv.cpu], ["Memory", bytes(inv.memory_bytes)],
        ["Disk free", `${bytes(inv.disk_free_bytes)} of ${bytes(inv.disk_total_bytes)}`],
        ["FileVault", inv.filevault], ["Gatekeeper", inv.gatekeeper], ["SIP", inv.sip],
        ["Console user", c.console_user || "—"], ["IP address", c.ip_address], ["Agent", c.agent_version],
        ["Enrolled", fmtTime(c.created_at)], ["Onboarded", c.onboarded ? "yes" : "no"],
        ["Inventory collected", fmtTime(c.inventory_at)],
      ];
      content = h("div", { class: "card" }, h("dl", { class: "kv" }, facts.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v || "—")])));
    } else if (tab === "apps") {
      content = h("div", { class: "card" }, table([
        ["Name", r => r.name], ["Version", r => r.version || "—"], ["Bundle ID", r => h("code", {}, r.bundle_id || "")],
        ["Path", r => h("span", { class: "muted" }, r.path)],
      ], inv.apps || []));
    } else {
      content = h("div", { class: "card" }, sessionTable(c.sessions));
    }
    main.replaceChildren(
      pageHead(h("span", {}, h("span", { class: `dot ${c.online ? "on" : ""}` }), c.name),
        btn("Run maintenance", () => queue("apply"), "primary"),
        btn("Detect only", () => queue("detect")),
        btn("Preview plan", () => previewPlan(id)),
        ME.role === "admin" ? btn("Run script", () => runScript(c)) : "",
        btn("Edit", () => editComputer(c)),
        ME.role === "admin" ? btn("Delete", () => confirmModal("Delete computer",
          `Remove ${c.name} and its history? The agent will stop working until re-enrolled.`,
          async () => { await api("DELETE", `/api/computers/${id}`); location.hash = "#/computers"; }), "danger") : ""),
      h("p", { class: "muted" }, [c.tenant_name, c.model, c.os_version, c.console_user && `user ${c.console_user}`,
        `last seen ${ago(c.last_seen)}`, c.tags.length && `tags: ${c.tags.map(t => t.name).join(", ")}`]
        .filter(Boolean).join(" · "),
        c.ninja ? [" · ", h("a", { href: c.ninja.url, target: "_blank", rel: "noopener" },
          `NinjaOne: ${c.ninja.name}${c.ninja.offline ? " (offline)" : ""} ↗`)] : ""),
      tabs, content);
  };
  const queue = async mode => {
    const r = await act(() => api("POST", `/api/computers/${id}/maintenance`, { mode }), "Session queued");
    location.hash = `#/sessions/${r.session_id}`;
  };
  await render();
  autoRefresh(render, 15000);
}

async function previewPlan(id) {
  const p = await api("GET", `/api/computers/${id}/plan`);
  openModal("Maintenance plan", h("div", {},
    p.warnings.map(w => h("p", { class: "error" }, w)),
    table([["#", (r) => p.actions.indexOf(r) + 1], ["Item", r => r.name], ["Type", r => r.item_type],
      ["Desired", r => DESIRED_LABEL[r.desired] || r.desired], ["Target version", r => r.target_version || "—"]], p.actions)),
    h("div", { class: "modal-foot" }, btn("Close", closeModal)));
}

function runScript(c) {
  formModal(`Run script on ${c.name}`, [
    { name: "name", label: "Name", default: "Ad-hoc script" },
    { name: "script", label: "Script (runs as root; zsh unless it starts with #!)", type: "code",
      placeholder: "#!/bin/bash\nsoftwareupdate --list" },
    { name: "timeout", label: "Timeout (seconds)", type: "number", default: 1800 },
  ], {}, async v => {
    const r = await api("POST", `/api/computers/${c.id}/script`, v);
    location.hash = `#/sessions/${r.session_id}`;
  }, "Run");
}

async function editComputer(c) {
  const [tenants, tags] = await Promise.all([api("GET", "/api/tenants"), api("GET", "/api/tags")]);
  const tagSet = new Set(c.tags.map(t => t.id));
  const fields = [
    { name: "name", label: "Display name" },
    { name: "tenant_id", label: "Tenant", type: "select", options: tenants.map(t => [t.id, t.name]) },
    ...tags.map(t => ({ name: `tag_${t.id}`, label: `Tag: ${t.name}`, type: "checkbox" })),
  ];
  const values = { name: c.name, tenant_id: c.tenant_id };
  tags.forEach(t => values[`tag_${t.id}`] = tagSet.has(t.id));
  formModal("Edit computer", fields, values, async v => {
    await api("PUT", `/api/computers/${c.id}`, {
      name: v.name, tenant_id: Number(v.tenant_id),
      tag_ids: tags.filter(t => v[`tag_${t.id}`]).map(t => t.id),
    });
    route();
  });
}

// Tenants --------------------------------------------------------------------

async function pageTenants(main) {
  const rows = await api("GET", "/api/tenants");
  const admin = ME.role === "admin";
  main.replaceChildren(
    pageHead("Tenants", admin ? btn("New tenant", () => editTenant(), "primary") : ""),
    h("p", { class: "muted" }, "Each tenant (client) has its own enrollment key. Run the install command on a Mac ",
      "(Terminal, or as a root shell script in your MDM) to enroll it into that tenant."),
    ...rows.map(t => h("div", { class: "card" },
      h("div", { class: "page-head" }, h("h1", { style: "font-size:16px" }, t.name, " ",
        h("span", { class: "muted" }, `· ${t.computer_count} computer(s)`)),
        h("div", { class: "actions" },
          btn("Computers", () => location.hash = "#/computers"),
          admin ? btn("Edit", () => editTenant(t)) : "",
          admin ? btn("Rotate key", () => confirmModal("Rotate enrollment key",
            "Old install commands will stop working. Already-enrolled Macs are unaffected.",
            async () => { await api("POST", `/api/tenants/${t.id}/rotate-key`); route(); }, "Rotate")) : "",
          admin ? btn("Delete", () => confirmModal("Delete tenant", `Delete ${t.name}?`,
            async () => { await api("DELETE", `/api/tenants/${t.id}`); route(); }), "danger") : "")),
      h("label", {}, "Install command", copyBox(t.install_command)),
      h("p", { class: "muted" }, t.onboarding_enabled ? "Onboarding: new Macs run a full maintenance session on enrollment."
        : "Onboarding disabled.", t.ninja_org_name ? ` · NinjaOne organization: ${t.ninja_org_name}` : "",
        t.notes ? ` · ${t.notes}` : ""))));
}

function editTenant(t) {
  formModal(t ? "Edit tenant" : "New tenant", [
    { name: "name", label: "Name", required: true },
    { name: "onboarding_enabled", label: "Run onboarding maintenance when a Mac enrolls", type: "checkbox", default: true },
    { name: "notes", label: "Notes", type: "textarea" },
  ], t || { onboarding_enabled: true }, async v => {
    await api(t ? "PUT" : "POST", t ? `/api/tenants/${t.id}` : "/api/tenants", v);
    route();
  });
}

// Software -------------------------------------------------------------------

async function pageSoftware(main, id) {
  if (id) return pageSoftwareDetail(main, Number(id));
  const rows = await api("GET", "/api/software");
  main.replaceChildren(
    pageHead("Software library", ME.role === "admin" ? btn("New software", () => editSoftware(), "primary") : ""),
    h("div", { class: "card" }, table([
      ["Name", r => h("a", { href: `#/software/${r.id}` }, r.name)],
      ["Publisher", r => r.publisher],
      ["Detection", r => h("span", {}, r.detection_type, " ", h("span", { class: "muted" }, r.detection_value))],
      ["Installer", r => r.installer_type],
      ["Latest version", r => r.latest_version || h("span", { class: "muted" }, r.download_url ? "rolling URL" : "—")],
      ["Deployments", r => r.deployment_count],
    ], rows, r => location.hash = `#/software/${r.id}`)));
}

const SOFTWARE_FIELDS = [
  { name: "name", label: "Name", required: true },
  { name: "publisher", label: "Publisher" },
  { name: "description", label: "Description", type: "textarea" },
  { name: "detection_type", label: "Detection", type: "select", options: [
    ["app", "App bundle (reads CFBundleShortVersionString)"], ["pkg", "Package receipt (pkgutil)"], ["script", "Script"]] },
  { name: "detection_value", label: "App path", placeholder: "/Applications/Example.app",
    show: v => v.detection_type === "app" },
  { name: "detection_value", label: "Package identifier", placeholder: "com.example.pkg",
    show: v => v.detection_type === "pkg" },
  { name: "detection_script", label: "Detection script", type: "code", show: v => v.detection_type === "script",
    hint: "Exit 0 and print the version when installed; exit non-zero (or print nothing) when not installed." },
  { name: "installer_type", label: "Installer type", type: "select", options: [
    ["pkg", "Package (.pkg)"], ["dmg", "Disk image (.dmg) — copies the app or runs the pkg inside"],
    ["zip", "Zip archive (.zip) — copies the app or runs the pkg inside"], ["script", "Custom install script"]] },
  { name: "download_url", label: "Rolling download URL (optional)",
    hint: "Used when no specific versions are added — e.g. a vendor's always-latest link." },
  { name: "install_script", label: "Install script", type: "code", show: v => v.installer_type === "script",
    hint: "Env: MS_INSTALLER_PATH (downloaded file, if a URL is set), MS_DOWNLOAD_DIR, MS_TARGET_VERSION, MS_CONSOLE_USER." },
  { name: "post_install_script", label: "Post-install script (optional)", type: "code" },
  { name: "uninstall_script", label: "Uninstall script (optional)", type: "code",
    hint: "If empty and detection is an app bundle, uninstall removes the app bundle." },
];

function editSoftware(s) {
  formModal(s ? `Edit ${s.name}` : "New software", SOFTWARE_FIELDS,
    s || { detection_type: "app", installer_type: "pkg" }, async v => {
      const r = await api(s ? "PUT" : "POST", s ? `/api/software/${s.id}` : "/api/software", v);
      location.hash = `#/software/${s ? s.id : r.id}`;
      route();
    });
}

async function pageSoftwareDetail(main, id) {
  const s = await api("GET", `/api/software/${id}`);
  const admin = ME.role === "admin";
  const script = (label, text) => text ? [h("h2", {}, label), h("pre", {}, text)] : [];
  main.replaceChildren(
    pageHead(s.name,
      btn("Deploy…", () => editDeployment(null, { item_type: "software", item_id: id }), "primary"),
      admin ? btn("Edit", () => editSoftware(s)) : "",
      admin ? btn("Delete", () => confirmModal("Delete software", `Delete ${s.name} and its deployments?`,
        async () => { await api("DELETE", `/api/software/${id}`); location.hash = "#/software"; }), "danger") : ""),
    h("div", { class: "grid" },
      h("div", { class: "card" }, h("h2", {}, "Details"), h("dl", { class: "kv" },
        [["Publisher", s.publisher], ["Detection", `${s.detection_type} ${s.detection_value}`],
          ["Installer", s.installer_type], ["Rolling URL", s.download_url]]
          .map(([k, v]) => [h("dt", {}, k), h("dd", {}, v || "—")])),
        s.description ? h("p", {}, s.description) : ""),
      h("div", { class: "card" }, h("h2", {}, "Versions"),
        table([
          ["Version", r => r.version],
          ["URL", r => h("span", { class: "muted", style: "word-break:break-all" }, r.url)],
          ["SHA-256", r => r.sha256 ? h("code", { title: r.sha256 }, r.sha256.slice(0, 12) + "…") : "—"],
          ["", r => admin ? btn("Remove", () => confirmModal("Remove version", `Remove ${r.version}?`,
            async () => { await api("DELETE", `/api/software/${id}/versions/${r.id}`); route(); }, "Remove"), "small danger") : ""],
        ], s.versions),
        admin ? h("div", { style: "margin-top:10px" }, btn("Add version", () => formModal("Add version", [
          { name: "version", label: "Version", required: true, placeholder: "1.2.3" },
          { name: "url", label: "Installer URL", required: true },
          { name: "sha256", label: "SHA-256 (recommended)", hint: "The agent refuses the download if it doesn't match." },
        ], {}, async v => { await api("POST", `/api/software/${id}/versions`, v); route(); }))) : "")),
    h("div", { class: "card" }, ...script("Detection script", s.detection_script), ...script("Install script", s.install_script),
      ...script("Post-install script", s.post_install_script), ...script("Uninstall script", s.uninstall_script),
      !(s.detection_script || s.install_script || s.post_install_script || s.uninstall_script)
        ? h("p", { class: "muted" }, "Uses built-in detection and installer handling; no custom scripts.") : ""));
}

// Tasks ----------------------------------------------------------------------

async function pageTasks(main) {
  const rows = await api("GET", "/api/tasks");
  const admin = ME.role === "admin";
  main.replaceChildren(
    pageHead("Maintenance tasks", admin ? btn("New task", () => editTask(), "primary") : ""),
    h("p", { class: "muted" }, "Tasks are desired-state configuration checks. The test script exits 0 when the Mac is ",
      "compliant; in Enforce mode the set script runs to fix it. Parameters are passed as environment variables."),
    h("div", { class: "card" }, table([
      ["Name", r => r.name],
      ["Description", r => h("span", { class: "muted" }, r.description)],
      ["Parameters", r => Object.keys(r.parameters).join(", ") || "—"],
      ["Remediates", r => r.set_script ? "yes" : "monitor only"],
      ["Deployments", r => r.deployment_count],
      ["", r => h("div", { class: "actions" },
        btn("Deploy…", () => editDeployment(null, { item_type: "task", item_id: r.id, parameters: r.parameters }), "small"),
        admin ? btn("Edit", () => editTask(r), "small") : "",
        admin ? btn("Delete", () => confirmModal("Delete task", `Delete ${r.name} and its deployments?`,
          async () => { await api("DELETE", `/api/tasks/${r.id}`); route(); }), "small danger") : "")],
    ], rows)));
}

function editTask(t) {
  formModal(t ? `Edit ${t.name}` : "New task", [
    { name: "name", label: "Name", required: true },
    { name: "description", label: "Description", type: "textarea" },
    { name: "test_script", label: "Test script (exit 0 = compliant)", type: "code" },
    { name: "set_script", label: "Set script (optional — remediates)", type: "code" },
    { name: "parameters", label: "Default parameters (NAME=value per line)", type: "params" },
  ], t || { parameters: {} }, async v => {
    await api(t ? "PUT" : "POST", t ? `/api/tasks/${t.id}` : "/api/tasks", v);
    route();
  });
}

// Deployments ----------------------------------------------------------------

async function pageDeployments(main) {
  const rows = await api("GET", "/api/deployments");
  main.replaceChildren(
    pageHead("Deployments", btn("New deployment", () => editDeployment(), "primary")),
    h("p", { class: "muted" }, "A deployment states what should be true on a set of Macs. When several deployments cover ",
      "the same item, the most specific target wins (computer › tag › tenant › all). Use “Ignore” to exclude."),
    h("div", { class: "card" }, table([
      ["Item", r => h("span", {}, r.item_name, " ", h("span", { class: "muted" }, r.item_type))],
      ["Desired state", r => badge(r.desired === "uninstalled" || r.desired === "ignore" ? "skipped" : "compliant",
        DESIRED_LABEL[r.desired] + (r.version ? ` ${r.version}` : ""))],
      ["Target", r => r.target_label],
      ["Parameters", r => formatParams(r.parameters) || "—"],
      ["Created", r => fmtTime(r.created_at)],
      ["", r => h("div", { class: "actions" },
        btn("Edit", () => editDeployment(r), "small"),
        btn("Delete", () => confirmModal("Delete deployment", `Stop managing ${r.item_name} for ${r.target_label}?`,
          async () => { await api("DELETE", `/api/deployments/${r.id}`); route(); }), "small danger"))],
    ], rows)));
}

async function editDeployment(d, preset = {}) {
  const [software, tasks, t] = await Promise.all([api("GET", "/api/software"), api("GET", "/api/tasks"), targetOptions()]);
  const versions = {};
  const loadVersions = async sid => {
    if (!sid || versions[sid]) return;
    versions[sid] = (await api("GET", `/api/software/${sid}`)).versions.map(v => [v.version, v.version]);
  };
  const values = d ? { ...d } : { item_type: "software", target_type: "all", desired: "latest", parameters: {}, ...preset };
  if (values.item_type === "software") {
    values.item_id = values.item_id ?? software[0]?.id;
    await loadVersions(values.item_id);
  }
  const fields = [
    { name: "item_type", label: "Type", type: "select", options: [["software", "Software"], ["task", "Maintenance task"]] },
    { name: "item_id", label: "Software", type: "select", options: software.map(s => [s.id, s.name]),
      show: v => v.item_type === "software" },
    { name: "item_id", label: "Task", type: "select", options: tasks.map(s => [s.id, s.name]),
      show: v => v.item_type === "task" },
    { name: "desired", label: "Desired state", type: "select", options: v => (v.item_type === "software"
      ? ["latest", "installed", "version", "uninstalled", "ignore"] : ["enforce", "monitor", "ignore"]).map(k => [k, DESIRED_LABEL[k]]) },
    { name: "version", label: "Version", type: "select", options: v => versions[v.item_id]?.length ? versions[v.item_id] : [["", "(no versions in library — add one first)"]],
      show: v => v.item_type === "software" && v.desired === "version" },
    { name: "parameters", label: "Parameter overrides (NAME=value per line)", type: "params", show: v => v.item_type === "task" },
    { name: "target_type", label: "Target", type: "select", options: t.types },
    { name: "target_id", label: "Which", type: "select", options: t.ids, show: v => v.target_type !== "all" },
  ];
  const submit = async v => {
    const body = { ...v, item_id: Number(v.item_id), target_id: v.target_id ? Number(v.target_id) : null };
    if (body.item_type === "task" && !["enforce", "monitor", "ignore"].includes(body.desired)) body.desired = "enforce";
    if (body.item_type === "software" && !["latest", "installed", "version", "uninstalled", "ignore"].includes(body.desired)) body.desired = "latest";
    await api(d ? "PUT" : "POST", d ? `/api/deployments/${d.id}` : "/api/deployments", body);
    toast("Deployment saved");
    if (location.hash.startsWith("#/deployments")) route();
  };
  formModal(d ? "Edit deployment" : "New deployment", fields, values, submit, "Save",
    async v => { if (v.item_type === "software") await loadVersions(v.item_id); });
}

// Schedules ------------------------------------------------------------------

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

async function pageSchedules(main) {
  const rows = await api("GET", "/api/schedules");
  const admin = ME.role === "admin";
  main.replaceChildren(
    pageHead("Schedules", admin ? btn("New schedule", () => editSchedule(), "primary") : ""),
    h("p", { class: "muted" }, "Schedules queue maintenance sessions at a server-local time. Offline Macs run the session ",
      "when they next check in."),
    h("div", { class: "card" }, table([
      ["Name", r => r.name],
      ["Target", r => r.target_label],
      ["When", r => `${r.time} · ${r.days.length === 7 ? "daily" : [...r.days].map(d => DAYS[d]).join(", ")}`],
      ["Mode", r => r.mode],
      ["Enabled", r => r.enabled ? badge("compliant", "on") : badge("skipped", "off")],
      ["Last run", r => r.last_run || "—"],
      ["", r => h("div", { class: "actions" },
        btn("Run now", async () => { const x = await act(() => api("POST", `/api/schedules/${r.id}/run`)); toast(`Queued ${x.queued} session(s)`); }, "small"),
        admin ? btn("Edit", () => editSchedule(r), "small") : "",
        admin ? btn("Delete", () => confirmModal("Delete schedule", `Delete ${r.name}?`,
          async () => { await api("DELETE", `/api/schedules/${r.id}`); route(); }), "small danger") : "")],
    ], rows)));
}

async function editSchedule(s) {
  const t = await targetOptions();
  const values = s ? { ...s } : { target_type: "all", time: "02:00", mode: "apply", enabled: true, days: "0123456" };
  DAYS.forEach((d, i) => values[`day${i}`] = values.days.includes(String(i)));
  formModal(s ? "Edit schedule" : "New schedule", [
    { name: "name", label: "Name", required: true, placeholder: "Nightly maintenance" },
    { name: "target_type", label: "Target", type: "select", options: t.types },
    { name: "target_id", label: "Which", type: "select", options: t.ids, show: v => v.target_type !== "all" },
    { name: "time", label: "Time (HH:MM, server time)", type: "time" },
    ...DAYS.map((d, i) => ({ name: `day${i}`, label: d, type: "checkbox" })),
    { name: "mode", label: "Mode", type: "select", options: [["apply", "Apply"], ["detect", "Detect only"]] },
    { name: "enabled", label: "Enabled", type: "checkbox" },
  ], values, async v => {
    const days = DAYS.map((_, i) => v[`day${i}`] ? String(i) : "").join("");
    await api(s ? "PUT" : "POST", s ? `/api/schedules/${s.id}` : "/api/schedules", {
      name: v.name, target_type: v.target_type, target_id: v.target_id ? Number(v.target_id) : null,
      time: v.time, days, mode: v.mode, enabled: v.enabled,
    });
    route();
  });
}

// Sessions -------------------------------------------------------------------

function sessionTable(rows) {
  return table([
    ["#", r => h("a", { href: `#/sessions/${r.id}` }, r.id)],
    ...(rows[0]?.computer_name ? [["Computer", r => r.computer_name]] : []),
    ["Kind", r => r.kind],
    ["Mode", r => r.mode],
    ["Status", r => badge(r.status)],
    ...(rows[0]?.action_count !== undefined ? [["Actions", r => `${r.action_count}${r.failed_count ? ` (${r.failed_count} failed)` : ""}`]] : []),
    ["By", r => h("span", { class: "muted" }, r.created_by)],
    ["Created", r => fmtTime(r.created_at)],
    ["Finished", r => fmtTime(r.finished_at)],
  ], rows, r => location.hash = `#/sessions/${r.id}`);
}

async function pageSessions(main, id) {
  if (id) return pageSession(main, Number(id));
  let status = "";
  const body = h("div", { class: "card" });
  const draw = async () => body.replaceChildren(sessionTable(await api("GET", "/api/sessions" + (status ? `?status=${status}` : ""))));
  main.replaceChildren(pageHead("Sessions"),
    h("div", { class: "toolbar" }, h("select", { onchange: e => { status = e.target.value; draw(); } },
      ["", "pending", "running", "succeeded", "failed", "cancelled"].map(s => h("option", { value: s }, s || "All statuses")))),
    body);
  await draw();
  autoRefresh(draw);
}

async function pageSession(main, id) {
  const render = async () => {
    const s = await api("GET", `/api/sessions/${id}`);
    const live = s.status === "pending" || s.status === "running";
    const logEl = h("pre", { class: "log" }, s.logs.map(l => [h("span", { class: "t" }, new Date(l.ts * 1000).toLocaleTimeString() + "  "), l.line, "\n"]));
    const wasAtBottom = (() => { const old = $(".log"); return !old || old.scrollTop + old.clientHeight >= old.scrollHeight - 20; })();
    main.replaceChildren(
      pageHead(`Session #${s.id}`, live ? btn("Cancel", () => confirmModal("Cancel session", "Stop this session? The current action finishes first.",
        async () => { await api("POST", `/api/sessions/${id}/cancel`); render(); }, "Cancel session"), "danger") : "",
        btn("Open computer", () => location.hash = `#/computers/${s.computer_id}`)),
      h("p", {}, badge(s.status), " ", h("span", { class: "muted" },
        `${s.computer_name} · ${s.kind} · ${s.mode} · created ${fmtTime(s.created_at)}${s.created_by ? ` by ${s.created_by}` : ""}`,
        s.finished_at ? ` · finished ${fmtTime(s.finished_at)}` : "")),
      h("div", { class: "card" }, h("h2", {}, "Actions"), table([
        ["#", r => r.seq + 1],
        ["Item", r => h("span", {}, r.name, " ", h("span", { class: "muted" }, r.item_type))],
        ["Desired", r => (DESIRED_LABEL[r.desired] || r.desired) + (r.target_version ? ` ${r.target_version}` : "")],
        ["Before", r => r.detected_before || "—"],
        ["After", r => r.detected_after || "—"],
        ["Status", r => badge(r.status)],
        ["Message", r => h("span", { class: "muted" }, r.message)],
      ], s.actions)),
      h("div", { class: "card" }, h("h2", {}, "Log"), logEl));
    if (wasAtBottom) logEl.scrollTop = logEl.scrollHeight;
    if (!live) clearInterval(refreshTimer);
  };
  await render();
  autoRefresh(render, 3000);
}

// NinjaOne -------------------------------------------------------------------

async function pageNinja(main) {
  const admin = ME.role === "admin";
  const [cfg, orgs, devices, tenants] = await Promise.all([
    admin ? api("GET", "/api/ninjaone") : null, api("GET", "/api/ninjaone/orgs"),
    api("GET", "/api/ninjaone/devices"), api("GET", "/api/tenants")]);
  const missing = devices.filter(d => !d.computer_id && d.tenant_name);
  const unlinked = devices.filter(d => !d.computer_id && !d.tenant_name).length;
  const selected = new Set();

  const sync = async () => {
    const r = await act(() => api("POST", "/api/ninjaone/sync"));
    toast(`Synced ${r.organizations} org(s), ${r.devices} Mac(s); ${r.devices_matched} matched, ${r.keys_pushed} key(s) pushed`
      + (r.warnings.length ? ` — ${r.warnings.length} warning(s)` : ""), r.warnings.length > 0);
    route();
  };
  const deploy = async body => {
    const r = await act(() => api("POST", "/api/ninjaone/deploy", body));
    openModal("Agent deployment via NinjaOne", h("div", {},
      h("p", {}, `Queued on ${r.queued.length} device(s). NinjaOne runs the script when each Mac is online; `,
        "it appears under Computers once it enrolls."),
      r.errors.length ? [h("h2", {}, "Errors"), table([["Device", e => e.name || e.device_id], ["Error", e => e.error]], r.errors)] : ""),
      h("div", { class: "modal-foot" }, btn("Close", closeModal)));
  };
  const downloadScript = async () => {
    const text = await api("GET", "/api/ninjaone/script");
    const a = h("a", { href: URL.createObjectURL(new Blob([text], { type: "text/x-shellscript" })),
      download: "ninjaone-install-macstate.sh" });
    document.body.append(a); a.click(); a.remove();
  };

  const settingsCard = admin ? h("div", { class: "card" }, h("h2", {}, "Connection"),
    h("dl", { class: "kv" },
      h("dt", {}, "Status"), h("dd", {}, cfg.configured ? badge("compliant", "configured") : badge("skipped", "not configured")),
      h("dt", {}, "Instance"), h("dd", {}, cfg.base_url),
      h("dt", {}, "Last sync"), h("dd", {}, cfg.last_sync ? fmtTime(Number(cfg.last_sync)) : "never"),
      cfg.last_sync_error ? [h("dt", {}, "Last problem"), h("dd", { class: "error" }, cfg.last_sync_error)] : ""),
    h("div", { class: "actions", style: "margin-top:12px" },
      btn("Configure", () => editNinja(cfg), "primary"),
      cfg.configured ? btn("Test connection", async () => {
        const r = await act(() => api("POST", "/api/ninjaone/test"));
        toast(`Connected — ${r.organizations} organization(s) visible`);
      }) : "",
      cfg.configured ? btn("Sync now", sync) : "",
      btn("Download install script", downloadScript))) : "";

  const orgCard = h("div", { class: "card" }, h("h2", {}, `Organizations (${orgs.length})`),
    h("p", { class: "muted" }, "Each NinjaOne organization maps to a MacState tenant. Linked tenants get their enrollment key ",
      "written to the organization custom field ", h("code", {}, cfg?.custom_field || "macstateEnrollmentKey"), "."),
    table([
      ["NinjaOne organization", r => r.name],
      ["Macs", r => `${r.agent_count}/${r.device_count} with agent`],
      ["MacState tenant", r => admin ? h("select", { onchange: async e => {
        const v = e.target.value;
        await act(() => api("PUT", `/api/ninjaone/orgs/${r.id}/link`,
          v === "__new" ? { create: true } : { tenant_id: v ? Number(v) : null }), "Link saved");
        route();
      } }, h("option", { value: "" }, "— not linked —"),
        tenants.map(t => h("option", { value: t.id, selected: t.id === r.tenant_id }, t.name)),
        h("option", { value: "__new" }, `+ Create tenant “${r.name}”`)) : (r.tenant_name || "—")],
    ], orgs));

  const devBody = h("div");
  const drawDevices = () => devBody.replaceChildren(table([
    ["", r => !r.computer_id && admin ? h("input", { type: "checkbox", checked: selected.has(r.id),
      onchange: e => { e.target.checked ? selected.add(r.id) : selected.delete(r.id); } }) : ""],
    ["Mac", r => h("a", { href: r.url, target: "_blank", rel: "noopener" }, r.name, " ↗")],
    ["Organization", r => r.org_name || r.organization_id],
    ["Serial", r => h("code", {}, r.serial || "—")],
    ["OS", r => r.os_name || "—"],
    ["NinjaOne", r => r.offline ? badge("skipped", "offline") : badge("compliant", "online")],
    ["MacState agent", r => r.computer_id
      ? h("a", { href: `#/computers/${r.computer_id}` }, badge("compliant", "installed"), " ", r.computer_name)
      : r.tenant_name ? badge("failed", "missing") : badge("skipped", "org not linked")],
  ], devices));
  drawDevices();

  main.replaceChildren(
    pageHead("NinjaOne", !admin && orgs.length ? btn("Sync now", sync) : ""),
    h("p", { class: "muted" }, "Sync organizations and Macs from NinjaOne, match them to MacState by serial number, and roll the ",
      "MacState agent out through NinjaOne scripting."),
    h("div", { class: "grid" }, settingsCard, admin ? setupCard(cfg) : ""),
    orgCard,
    h("div", { class: "card" }, h("div", { class: "page-head" },
      h("h1", { style: "font-size:15px" }, `Macs in NinjaOne (${devices.length}) · ${missing.length} ready to deploy`
        + (unlinked ? ` · ${unlinked} in unlinked orgs` : "")),
      admin ? h("div", { class: "actions" },
        btn("Deploy to selected", () => selected.size ? deploy({ device_ids: [...selected] }) : toast("Select Macs first", true)),
        missing.length ? btn(`Deploy to all ${missing.length} missing`, () => confirmModal("Deploy MacState agent",
          `Run the install script through NinjaOne on ${missing.length} Mac(s)?`, () => deploy({ all_missing: true }), "Deploy"), "primary") : "")
        : ""),
      devBody));
}

function setupCard(cfg) {
  const step = (n, ...body) => h("li", { style: "margin-bottom:8px" }, ...body);
  return h("div", { class: "card" }, h("h2", {}, "Setup"), h("ol", { style: "margin:0;padding-left:18px" },
    step(1, "In NinjaOne: Administration › Apps › API › Client App IDs › Add. Platform ",
      h("b", {}, "API Services (machine-to-machine)"), ", scopes Monitoring + Management, grant type Client Credentials."),
    step(2, "Create an ", h("b", {}, "organization"), " custom field (text) named ", h("code", {}, cfg.custom_field),
      " with API read/write and script read access."),
    step(3, "Download the install script and add it to the NinjaOne Automation Library as a macOS shell script, run as System. ",
      "Put its script ID in Configure."),
    step(4, "Run it from a NinjaOne policy for Macs (e.g. at agent install + daily) for hands-off rollout, ",
      "or use the Deploy buttons below.")));
}

function editNinja(cfg) {
  const regions = Object.entries(cfg.regions);
  const isCustom = !cfg.regions[cfg.base_url];
  formModal("NinjaOne connection", [
    { name: "region", label: "Instance", type: "select", options: [...regions, ["custom", "Other URL…"]] },
    { name: "base_url", label: "Instance URL", show: v => v.region === "custom", placeholder: "https://example.rmmservice.eu" },
    { name: "client_id", label: "Client ID" },
    { name: "client_secret", label: cfg.client_secret_set ? "Client secret (leave blank to keep current)" : "Client secret",
      type: "password" },
    { name: "scope", label: "OAuth scopes", hint: "Space-separated; must match the scopes enabled on the client app." },
    { name: "custom_field", label: "Organization custom field for enrollment key" },
    { name: "push_keys", label: "Write tenant enrollment keys to that custom field on sync", type: "checkbox" },
    { name: "auto_create_tenants", label: "Create a MacState tenant for every unlinked NinjaOne organization", type: "checkbox" },
    { name: "device_classes", label: "NinjaOne device classes to sync", hint: "Comma-separated NinjaOne node classes. Macs are MAC." },
    { name: "script_id", label: "NinjaOne script ID of the MacState install script", placeholder: "e.g. 142" },
    { name: "run_as", label: "Run script as", hint: "system (recommended), loggedonuser, or a NinjaOne credential ID." },
    { name: "sync_interval", label: "Automatic sync interval (seconds, 0 = off)" },
  ], { ...cfg, region: isCustom ? "custom" : cfg.base_url, client_secret: "",
       push_keys: cfg.push_keys === "1", auto_create_tenants: cfg.auto_create_tenants === "1" }, async v => {
    const body = { ...v, base_url: v.region === "custom" ? v.base_url : v.region };
    delete body.region;
    await api("PUT", "/api/ninjaone", body);
    toast("NinjaOne settings saved");
    route();
  });
}

// Settings -------------------------------------------------------------------

async function pageSettings(main) {
  const admin = ME.role === "admin";
  const [tags, users, dash] = await Promise.all([api("GET", "/api/tags"), admin ? api("GET", "/api/users") : [],
    api("GET", "/api/dashboard")]);
  main.replaceChildren(
    pageHead("Settings"),
    h("div", { class: "grid" },
      h("div", { class: "card" }, h("h2", {}, "Tags"),
        h("p", { class: "muted" }, "Tag computers to target deployments and schedules at groups across tenants."),
        table([["Name", r => r.name], ["Computers", r => r.computer_count],
          ["", r => admin ? btn("Delete", () => confirmModal("Delete tag", `Delete tag ${r.name} and deployments targeting it?`,
            async () => { await api("DELETE", `/api/tags/${r.id}`); route(); }), "small danger") : ""]], tags),
        h("div", { style: "margin-top:10px" }, btn("New tag", () => formModal("New tag", [{ name: "name", label: "Name", required: true }], {},
          async v => { await api("POST", "/api/tags", v); route(); })))),
      h("div", { class: "card" }, h("h2", {}, "Agent"),
        h("dl", { class: "kv" }, h("dt", {}, "Build"), h("dd", {}, dash.agent.version || "not built"),
          ...Object.entries(dash.agent.sha256).map(([a, s]) => [h("dt", {}, a), h("dd", {}, h("code", {}, s.slice(0, 16) + "…"))])),
        h("p", { class: "muted" }, "Agents update themselves to this build on their next idle check-in. Rebuild with ",
          h("code", {}, "./build-agent.sh"), "."),
        h("p", { class: "muted" }, "Uninstall from a Mac: ", h("code", {}, "sudo '/Library/Application Support/MacState/macstate-agent' uninstall")))),
    admin ? h("div", { class: "card" }, h("h2", {}, "Users"),
      table([["Username", r => r.username], ["Role", r => r.role],
        ["", r => h("div", { class: "actions" },
          btn("Set password", () => formModal(`Password for ${r.username}`, [{ name: "password", label: "New password", type: "password" }], {},
            async v => { await api("POST", `/api/users/${r.id}/password`, v); toast("Password changed"); }), "small"),
          r.id !== ME.id ? btn("Delete", () => confirmModal("Delete user", `Delete ${r.username}?`,
            async () => { await api("DELETE", `/api/users/${r.id}`); route(); }), "small danger") : "")]], users),
      h("div", { style: "margin-top:10px" }, btn("New user", () => formModal("New user", [
        { name: "username", label: "Username", required: true },
        { name: "password", label: "Password (min 8 chars)", type: "password" },
        { name: "role", label: "Role", type: "select", options: [["technician", "Technician — run maintenance, manage deployments"], ["admin", "Admin — everything"]] },
      ], { role: "technician" }, async v => { await api("POST", "/api/users", v); route(); }))))
      : h("div", { class: "card" }, h("h2", {}, "Your account"),
        btn("Change password", () => formModal("Change password", [{ name: "password", label: "New password", type: "password" }], {},
          async v => { await api("POST", `/api/users/${ME.id}/password`, v); toast("Password changed"); }))),
  );
}

// ---- boot ------------------------------------------------------------------

function showLogin() {
  clearInterval(refreshTimer);
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
}

async function boot() {
  try {
    ME = await api("GET", "/api/me");
  } catch { showLogin(); return; }
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#whoami").textContent = `${ME.username} (${ME.role})`;
  route();
}

$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("POST", "/api/login", { username: f.get("username"), password: f.get("password") });
    $("#login-error").textContent = "";
    boot();
  } catch (ex) { $("#login-error").textContent = ex.message; }
});
$("#logout").addEventListener("click", async e => {
  e.preventDefault();
  await api("POST", "/api/logout");
  showLogin();
});
window.addEventListener("hashchange", route);
boot();
