(() => {
  "use strict";

  const key = new URLSearchParams(location.search).get("k") || "";
  const withKey = (path) => `${path}${path.includes("?") ? "&" : "?"}k=${encodeURIComponent(key)}`;
  const $ = (id) => document.getElementById(id);

  const state = {
    version: "",
    items: [],
    devices: [],
    active: [],
    folder: "",
    phoneUrl: null,
    update: null,
  };
  let firstState = true;

  // ---- utilidades ------------------------------------------------------------

  function formatSize(bytes) {
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
      bytes /= 1024;
      i++;
    }
    return (i === 0 ? bytes.toFixed(0) : bytes.toFixed(1)).replace(".", ",") + " " + units[i];
  }

  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
  }

  function folderShortName(path) {
    const parts = (path || "").split(/[\\/]/).filter(Boolean);
    return parts.slice(-2).join(" › ") || "Carpeta";
  }

  function action(name, extra = {}) {
    return fetch(withKey("/desktop/action"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: name, ...extra }),
    }).then((r) => r.ok);
  }

  function toast(text, kind = "ok") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.innerHTML = '<i class="dot"></i><span></span>';
    el.querySelector("span").textContent = text;
    $("toasts").append(el);
    setTimeout(() => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 300);
    }, 3800);
  }

  // ---- vistas ------------------------------------------------------------------

  function isConnected() {
    return state.devices.length > 0 || state.items.length > 0 || state.active.length > 0;
  }

  function renderView() {
    const connected = isConnected();
    document.body.classList.toggle("is-connected", connected);
    document.body.classList.toggle("is-waiting", !connected);
    $("show-qr").hidden = !connected;

    const pill = $("device-pill");
    if (state.devices.length) {
      const unique = [...new Set(state.devices)];
      $("device-name").textContent =
        state.devices.length === 1 ? `${unique[0]} conectado` : `${state.devices.length} móviles conectados`;
      pill.hidden = false;
    } else {
      pill.hidden = true;
    }
  }

  function renderQr() {
    const url = state.phoneUrl;
    $("no-network").hidden = Boolean(url);
    $("phone-url").textContent = url ? url.replace(/\?t=.*$/, "") : "";
    if (url) {
      const src = withKey(`/desktop/qr.svg?v=${encodeURIComponent(url)}`);
      $("qr").src = src;
      $("qr-modal-img").src = src;
    } else {
      $("qr").removeAttribute("src");
    }
  }

  function renderFolder() {
    $("folder-name").textContent = folderShortName(state.folder);
    $("folder-path").textContent = state.folder ? `Se guardan en ${state.folder}` : "";
  }

  function renderStats() {
    const photos = state.items.filter((i) => i.kind === "photo").length;
    const videos = state.items.filter((i) => i.kind === "video").length;
    const bytes = state.items.reduce((sum, i) => sum + (i.skipped ? 0 : i.size), 0);
    const skipped = state.items.filter((i) => i.skipped).length;
    const stats = [];
    if (photos) stats.push([photos, photos === 1 ? "foto" : "fotos"]);
    if (videos) stats.push([videos, videos === 1 ? "vídeo" : "vídeos"]);
    if (bytes) stats.push([formatSize(bytes), "recibidos"]);
    $("stats").replaceChildren(
      ...stats.map(([value, label]) => {
        const el = document.createElement("span");
        el.className = "stat";
        el.innerHTML = "<b></b><span></span>";
        el.querySelector("b").textContent = value;
        el.querySelector("span").textContent = label;
        return el;
      })
    );

    const total = state.items.length;
    $("summary").textContent = total
      ? `${plural(total, "archivo", "archivos")} en esta sesión` +
        (skipped ? ` · ${plural(skipped, "ya estaba", "ya estaban")} en el ordenador` : "") +
        " · haz clic en una foto para abrirla"
      : "Elige fotos en el móvil y aparecerán aquí al instante.";
    $("empty").hidden = total > 0;
  }

  function renderTransfers() {
    const active = state.active;
    $("transfers").hidden = active.length === 0;
    if (!active.length) return;
    const size = active.reduce((s, a) => s + a.size, 0) || 1;
    const done = active.reduce((s, a) => s + a.offset, 0);
    const percent = Math.min(100, Math.floor((done / size) * 100));
    $("transfers-title").textContent = `Recibiendo ${plural(active.length, "archivo", "archivos")}`;
    $("transfers-meta").textContent = `${formatSize(done)} de ${formatSize(size)} · ${percent} %`;
    $("transfers-bar").style.width = `${percent}%`;
    $("transfers-list").replaceChildren(
      ...active.slice(0, 6).map((a) => {
        const li = document.createElement("li");
        li.innerHTML = "<span></span><span></span>";
        li.children[0].textContent = a.name;
        li.children[1].textContent = `${Math.floor((a.offset / (a.size || 1)) * 100)} %`;
        return li;
      })
    );
  }

  const PLAY = '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z"/></svg>';

  function placeholder(item) {
    const ph = document.createElement("span");
    ph.className = "ph";
    ph.innerHTML =
      item.kind === "video"
        ? '<svg viewBox="0 0 24 24"><path d="m16 13 5.2 3.5a.5.5 0 0 0 .8-.4V7.9a.5.5 0 0 0-.8-.4L16 11"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>'
        : '<svg viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/></svg>';
    const label = document.createElement("span");
    label.textContent = item.ext || "ARCHIVO";
    ph.append(label);
    return ph;
  }

  function tile(item, isNew) {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "tile" + (isNew ? " is-new" : "");
    el.title = `${item.name} · ${formatSize(item.size)}`;

    const img = document.createElement("img");
    img.alt = "";
    img.decoding = "async";
    img.src = withKey(`/desktop/thumb/${item.id}`);
    img.onerror = () => img.replaceWith(placeholder(item));
    el.append(img);

    if (item.kind === "video") {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.innerHTML = `${PLAY}<span>Vídeo</span>`;
      el.append(badge);
    }
    if (item.skipped) {
      const badge = document.createElement("span");
      badge.className = "badge skipped";
      badge.textContent = "Ya estaba";
      el.append(badge);
    }
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = item.name;
    el.append(name);

    el.addEventListener("click", async () => {
      if (!(await action("open", { id: item.id }))) toast(`${item.name} ya no está en la carpeta`, "error");
    });
    return el;
  }

  function renderGallery() {
    const items = [...state.items].reverse(); // más recientes primero
    $("gallery").replaceChildren(...items.map((item) => tile(item, false)));
  }

  function renderUpdate() {
    const update = state.update;
    $("update-banner").hidden = !update;
    if (!update) return;
    $("update-text").textContent = `Hay una versión nueva de PhotoBridge: v${update.version}`;
    $("update-button").disabled = update.busy;
  }

  function renderAll() {
    $("version").textContent = state.version ? `v${state.version}` : "";
    renderView();
    renderQr();
    renderFolder();
    renderStats();
    renderTransfers();
    renderGallery();
    renderUpdate();
  }

  // ---- eventos en directo -------------------------------------------------------

  const events = new EventSource(withKey("/desktop/events"));

  events.addEventListener("open", () => {
    document.body.classList.add("is-online");
    $("link-text").textContent = "PhotoBridge en marcha · tus fotos no salen de tu red";
  });
  events.addEventListener("error", () => {
    document.body.classList.remove("is-online");
    $("link-text").textContent = "Reconectando…";
  });

  events.addEventListener("state", (e) => {
    Object.assign(state, JSON.parse(e.data));
    renderAll();
    if (firstState && state.update) askUpdate();
    firstState = false;
  });

  events.addEventListener("devices", (e) => {
    const data = JSON.parse(e.data);
    const wasConnected = isConnected();
    state.devices = data.devices;
    renderView();
    if (data.new) toast(`${data.new} conectado`);
    if (!wasConnected && isConnected()) $("qr-modal").hidden = true;
  });

  events.addEventListener("progress", (e) => {
    state.active = JSON.parse(e.data).active;
    renderView();
    renderTransfers();
  });

  events.addEventListener("item", (e) => {
    const data = JSON.parse(e.data);
    state.active = data.active;
    if (data.item) {
      state.items.push(data.item);
      $("gallery").prepend(tile(data.item, true));
    }
    renderView();
    renderStats();
    renderTransfers();
  });

  events.addEventListener("qr", (e) => {
    state.phoneUrl = JSON.parse(e.data).phoneUrl;
    renderQr();
  });

  events.addEventListener("folder", (e) => {
    state.folder = JSON.parse(e.data).folder;
    renderFolder();
    toast("Carpeta cambiada");
  });

  events.addEventListener("app_error", (e) => {
    toast(JSON.parse(e.data).message, "error");
  });

  events.addEventListener("update", (e) => {
    state.update = JSON.parse(e.data);
    renderUpdate();
    askUpdate();
  });

  events.addEventListener("update_progress", (e) => {
    const { percent } = JSON.parse(e.data);
    $("update-bar").hidden = false;
    $("update-bar-fill").style.width = `${percent}%`;
    $("update-text").textContent = `Descargando la actualización… ${percent} %`;
  });

  events.addEventListener("update_error", (e) => {
    const { message } = JSON.parse(e.data);
    if (state.update) state.update.busy = false;
    $("update-bar").hidden = true;
    $("update-text").textContent = "No se pudo actualizar";
    $("update-button").disabled = false;
    $("update-button").textContent = "Reintentar";
    toast(`La actualización ha fallado: ${message}`, "error");
  });

  events.addEventListener("closing", () => {
    events.close();
    $("closing").hidden = false;
    setTimeout(() => window.close(), 900);
  });

  // ---- acciones -------------------------------------------------------------------

  function askUpdate() {
    if (!state.update || state.update.busy) return;
    $("update-modal-text").textContent =
      `Ya está disponible la v${state.update.version} (ahora tienes la v${state.update.current}). ` +
      "Se descarga en unos segundos y PhotoBridge se vuelve a abrir solo.";
    $("update-modal").hidden = false;
  }

  function startUpdate() {
    $("update-modal").hidden = true;
    if (state.update) state.update.busy = true;
    $("update-button").disabled = true;
    $("update-text").textContent = "Preparando la actualización…";
    action("update");
  }

  $("open-folder").addEventListener("click", () => action("open_folder"));
  $("choose-folder").addEventListener("click", () => action("choose_folder"));
  $("show-qr").addEventListener("click", () => ($("qr-modal").hidden = false));
  $("qr-close").addEventListener("click", () => ($("qr-modal").hidden = true));
  $("qr-modal").addEventListener("click", (e) => {
    if (e.target === $("qr-modal")) $("qr-modal").hidden = true;
  });
  $("update-button").addEventListener("click", startUpdate);
  $("update-now").addEventListener("click", startUpdate);
  $("update-later").addEventListener("click", () => ($("update-modal").hidden = true));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      $("qr-modal").hidden = true;
      $("update-modal").hidden = true;
    }
  });
  // Evitar el menú contextual y el zoom del navegador: es una app, no una web
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("wheel", (e) => e.ctrlKey && e.preventDefault(), { passive: false });
})();
