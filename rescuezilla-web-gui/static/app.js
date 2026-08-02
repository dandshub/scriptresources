"use strict";

const $ = (sel) => document.querySelector(sel);
let me = null;                 // {username, role}
let current = null;            // {id, path}
let pollTimer = null;
let lastImages = [];           // cache for admin selectors

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401) { showLogin(); throw new Error("not authenticated"); }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}

function jpost(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function fmtSize(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(i ? 1 : 0) + " " + u[i];
}
function fmtDate(sec) {
  if (!sec) return "";
  return new Date(sec * 1000).toISOString().slice(0, 16).replace("T", " ");
}

// ---- auth -----------------------------------------------------------------

function showLogin() {
  me = null;
  $("#login").classList.remove("hidden");
  $("#app").classList.add("hidden");
  $("#adminDrawer").classList.add("hidden");
}
function showApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#whoami").textContent = `${me.username} (${me.role})`;
  $("#adminBtn").classList.toggle("hidden", me.role !== "admin");
  loadImages();
  loadMounts();
}

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#loginError").textContent = "";
  try {
    me = await jpost("/api/login", {
      username: $("#loginUser").value,
      password: $("#loginPass").value,
    });
    showApp();
  } catch (err) { $("#loginError").textContent = err.message; }
});

async function logout() {
  try { await jpost("/api/logout", {}); } catch (_) {}
  showLogin();
}

// ---- images / mounts ------------------------------------------------------

async function loadImages() {
  const data = await api("/api/images");
  lastImages = data.images;
  $("#imagesDir").textContent = data.images_dir;
  const box = $("#images");
  box.innerHTML = "";
  if (!data.images.length) {
    box.innerHTML = '<p class="muted">No images available to you.</p>';
    return;
  }
  for (const img of data.images) {
    const wrap = document.createElement("div");
    wrap.className = "image";
    wrap.innerHTML = `<div class="image-name">${img.name}</div>`;
    for (const p of img.partitions) {
      const row = document.createElement("div");
      row.className = "part";
      let label = `${p.name} · ${p.fstype}`;
      if (p.label) label += ` · ${p.label}`;
      if (p.size_bytes) label += ` · ${fmtSize(p.size_bytes)}`;
      if (p.is_bitlocker) label += ` 🔒`;
      row.innerHTML = `<span>${label}</span>`;
      const btn = document.createElement("button");
      btn.textContent = "Mount";
      const blocked = p.is_bitlocker && !p.has_bitlocker_key;
      btn.disabled = !p.supported || blocked;
      if (!p.supported) btn.title = `Unsupported compressor: ${p.compressor}`;
      else if (blocked) btn.title = "BitLocker key required (ask an admin)";
      btn.onclick = () => mount(img.path, p.name);
      row.appendChild(btn);
      wrap.appendChild(row);
    }
    box.appendChild(wrap);
  }
  if (me && me.role === "admin") refreshAdminSelectors();
}

async function mount(imagePath, part) {
  try {
    await jpost("/api/mount", { image_path: imagePath, part });
    startPolling();
  } catch (e) { alert("Mount failed: " + e.message); }
}

async function loadMounts() {
  let data;
  try { data = await api("/api/mounts"); } catch (_) { return; }
  const box = $("#mounts");
  box.innerHTML = "";
  let busy = false;
  for (const m of data.mounts) {
    if (["pending", "mounting", "unmounting"].includes(m.state)) busy = true;
    const row = document.createElement("div");
    row.className = "mount state-" + m.state;
    const info = document.createElement("div");
    info.className = "mount-info";
    info.innerHTML =
      `<b>${m.image_name} / ${m.part}</b> <span class="badge">${m.state}</span>` +
      `<div class="muted small">${m.message || ""}</div>`;
    if (m.state === "mounted") {
      info.style.cursor = "pointer";
      info.onclick = () => openMount(m.id);
    }
    row.appendChild(info);
    const btn = document.createElement("button");
    btn.textContent = "Unmount";
    btn.onclick = () => unmount(m.id);
    row.appendChild(btn);
    box.appendChild(row);
  }
  if (!busy) stopPolling();
}

function startPolling() {
  loadMounts();
  if (!pollTimer) pollTimer = setInterval(loadMounts, 1500);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function unmount(id) {
  try {
    await jpost("/api/unmount", { id });
    if (current && current.id === id) { current = null; renderBrowser([], ""); }
    loadMounts();
  } catch (e) { alert("Unmount failed: " + e.message); }
}

function openMount(id) { current = { id, path: "" }; browse(""); }

async function browse(path) {
  if (!current) return;
  try {
    const data = await api(
      `/api/browse?id=${current.id}&path=${encodeURIComponent(path)}`);
    current.path = data.path;
    renderBrowser(data.entries, data.path);
  } catch (e) { alert("Browse failed: " + e.message); }
}

function renderBrowser(entries, path) {
  $("#empty").style.display = current ? "none" : "block";
  const crumbs = $("#crumbs");
  crumbs.innerHTML = "";
  if (current) {
    const mk = (label, p) => {
      const a = document.createElement("a");
      a.textContent = label; a.onclick = () => browse(p);
      return a;
    };
    crumbs.appendChild(mk("root", ""));
    let acc = "";
    for (const seg of path.split("/").filter(Boolean)) {
      acc = acc ? acc + "/" + seg : seg;
      crumbs.appendChild(document.createTextNode(" / "));
      crumbs.appendChild(mk(seg, acc));
    }
    const dl = document.createElement("button");
    dl.className = "zip";
    dl.textContent = "Download folder (.zip)";
    dl.onclick = () => (window.location =
      `/api/download-folder?id=${current.id}&path=${encodeURIComponent(path)}`);
    crumbs.appendChild(dl);
  }

  const tbody = $("#files tbody");
  tbody.innerHTML = "";
  if (current && path) {
    const up = path.split("/").slice(0, -1).join("/");
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><a class="dir">📁 ..</a></td><td></td><td></td><td></td>`;
    tr.querySelector("a").onclick = () => browse(up);
    tbody.appendChild(tr);
  }
  for (const e of entries) {
    const tr = document.createElement("tr");
    const nameCell = document.createElement("td");
    if (e.is_dir) {
      const a = document.createElement("a");
      a.className = "dir"; a.textContent = "📁 " + e.name;
      a.onclick = () => browse(path ? path + "/" + e.name : e.name);
      nameCell.appendChild(a);
    } else {
      nameCell.textContent = (e.is_symlink ? "🔗 " : "📄 ") + e.name;
    }
    tr.appendChild(nameCell);
    tr.insertCell().textContent = fmtSize(e.size);
    tr.insertCell().textContent = fmtDate(e.mtime);
    const act = tr.insertCell();
    if (!e.is_dir && !e.is_symlink) {
      const rel = path ? path + "/" + e.name : e.name;
      const a = document.createElement("a");
      a.textContent = "Download";
      a.href = `/api/download?id=${current.id}&path=${encodeURIComponent(rel)}`;
      act.appendChild(a);
    }
    tbody.appendChild(tr);
  }
}

// ---- admin ----------------------------------------------------------------

function toggleAdmin() {
  const d = $("#adminDrawer");
  d.classList.toggle("hidden");
  if (!d.classList.contains("hidden")) { loadUsers(); refreshAdminSelectors(); }
}

async function loadUsers() {
  const data = await api("/api/admin/users");
  const box = $("#userList");
  box.innerHTML = "";
  const userSel = $("#shareUser");
  userSel.innerHTML = "";
  for (const u of data.users) {
    const row = document.createElement("div");
    row.className = "urow";
    row.innerHTML = `<span>${u.username} <em class="muted">(${u.role})</em></span>`;
    const pw = document.createElement("button");
    pw.className = "ghost"; pw.textContent = "Reset pw";
    pw.onclick = async () => {
      const np = prompt(`New password for ${u.username}:`);
      if (np) { await jpost("/api/admin/users/password",
        { username: u.username, password: np }); alert("updated"); }
    };
    row.appendChild(pw);
    if (u.username !== me.username) {
      const del = document.createElement("button");
      del.textContent = "Delete";
      del.onclick = async () => {
        if (confirm(`Delete ${u.username}?`)) {
          await jpost("/api/admin/users/delete",
            { image_name: "", username: u.username });
          loadUsers();
        }
      };
      row.appendChild(del);
    }
    box.appendChild(row);
    const opt = document.createElement("option");
    opt.value = u.username; opt.textContent = u.username;
    userSel.appendChild(opt);
  }
}

$("#userForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await jpost("/api/admin/users", {
      username: $("#newUser").value,
      password: $("#newPass").value,
      role: $("#newRole").value,
    });
    $("#newUser").value = ""; $("#newPass").value = "";
    loadUsers();
  } catch (err) { alert(err.message); }
});

function refreshAdminSelectors() {
  const fill = (sel) => {
    const el = $(sel); const prev = el.value;
    el.innerHTML = "";
    for (const img of lastImages) {
      const o = document.createElement("option");
      o.value = img.name; o.textContent = img.name;
      el.appendChild(o);
    }
    el.value = prev;
  };
  fill("#shareImage"); fill("#blImage");
  updateShareList(); updateBlParts();
}

$("#shareImage").addEventListener("change", updateShareList);
function updateShareList() {
  const name = $("#shareImage").value;
  const img = lastImages.find((i) => i.name === name);
  const box = $("#shareList");
  box.innerHTML = "";
  if (!img) return;
  for (const u of img.shared_with || []) {
    const row = document.createElement("div");
    row.className = "urow";
    row.innerHTML = `<span>${u}</span>`;
    const rm = document.createElement("button");
    rm.textContent = "Remove";
    rm.onclick = async () => {
      await jpost("/api/admin/unshare", { image_name: name, username: u });
      await loadImages(); updateShareList();
    };
    row.appendChild(rm);
    box.appendChild(row);
  }
}

$("#shareForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await jpost("/api/admin/share", {
      image_name: $("#shareImage").value,
      username: $("#shareUser").value,
    });
    await loadImages(); updateShareList();
  } catch (err) { alert(err.message); }
});

$("#blImage").addEventListener("change", updateBlParts);
function updateBlParts() {
  const name = $("#blImage").value;
  const img = lastImages.find((i) => i.name === name);
  const sel = $("#blPart");
  sel.innerHTML = "";
  if (!img) return;
  for (const p of img.partitions) {
    const o = document.createElement("option");
    const tag = p.has_bitlocker_key ? " (key set)" : (p.is_bitlocker ? " 🔒" : "");
    o.value = p.name; o.textContent = p.name + tag;
    sel.appendChild(o);
  }
}

$("#blForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await jpost("/api/admin/bitlocker", {
      image_name: $("#blImage").value,
      part: $("#blPart").value,
      value: $("#blValue").value,
      key_type: $("#blType").value,
    });
    $("#blValue").value = "";
    await loadImages(); updateBlParts();
    alert("BitLocker key saved");
  } catch (err) { alert(err.message); }
});

// ---- boot -----------------------------------------------------------------

(async function boot() {
  try {
    me = await api("/api/me");
    showApp();
  } catch (_) {
    showLogin();
  }
})();
