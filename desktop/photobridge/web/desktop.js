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
  let selecting = false;
  const selected = new Set();
  const viewer = { open: false, id: null };

  const ICONS = {
    play: '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>',
    trash:
      '<svg viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>',
    video:
      '<svg viewBox="0 0 24 24"><path d="m16 13 5.2 3.5a.5.5 0 0 0 .8-.4V7.9a.5.5 0 0 0-.8-.4L16 11"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>',
    image:
      '<svg viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/></svg>',
  };

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

  function formatDate(ms) {
    if (!ms) return "";
    return new Date(ms).toLocaleString("es-ES", { dateStyle: "medium", timeStyle: "short" });
  }

  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
  }

  function folderShortName(path) {
    const parts = (path || "").split(/[\\/]/).filter(Boolean);
    return parts.slice(-2).join(" › ") || "Carpeta";
  }

  async function action(name, extra = {}) {
    try {
      const res = await fetch(withKey("/desktop/action"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: name, ...extra }),
      });
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, data };
    } catch {
      return { ok: false, data: {} };
    }
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

  const itemById = (id) => state.items.find((i) => i.id === id);
  const ordered = () => [...state.items].reverse(); // más recientes primero, como en la galería

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
        (skipped ? ` · ${plural(skipped, "ya estaba", "ya estaban")} en el ordenador` : "")
      : "Elige fotos en el móvil y aparecerán aquí al instante.";
    $("gallery-hint").hidden = total === 0 || selecting;
    $("select-toggle").hidden = total === 0;
    $("empty").hidden = total > 0;
    if (total === 0 && selecting) setSelecting(false);
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

  function placeholder(item) {
    const ph = document.createElement("span");
    ph.className = "ph";
    ph.innerHTML = item.kind === "video" ? ICONS.video : ICONS.image;
    const label = document.createElement("span");
    label.textContent = item.ext || "ARCHIVO";
    ph.append(label);
    return ph;
  }

  // ---- galería -------------------------------------------------------------------

  function tile(item, isNew) {
    const el = document.createElement("div");
    el.className = "tile" + (isNew ? " is-new" : "") + (selected.has(item.id) ? " selected" : "");
    el.dataset.id = item.id;
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.title = `${item.name} · ${formatSize(item.size)}`;

    const img = document.createElement("img");
    img.alt = "";
    img.decoding = "async";
    img.loading = "lazy";
    img.src = withKey(`/desktop/thumb/${item.id}`);
    img.onerror = () => img.replaceWith(placeholder(item));
    el.append(img);

    if (item.kind === "video") {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.innerHTML = `${ICONS.play}<span>Vídeo</span>`;
      el.append(badge);
    }
    if (item.skipped) {
      const badge = document.createElement("span");
      badge.className = "badge skipped";
      badge.textContent = "Ya estaba";
      el.append(badge);
    }

    const check = document.createElement("button");
    check.type = "button";
    check.className = "check";
    check.title = "Seleccionar";
    check.innerHTML = ICONS.check;
    check.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!selecting) setSelecting(true);
      toggleSelected(item.id);
    });
    el.append(check);

    const del = document.createElement("button");
    del.type = "button";
    del.className = "tile-delete";
    del.title = "Borrar";
    del.innerHTML = ICONS.trash;
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      confirmDelete([item.id]);
    });
    el.append(del);

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = item.name;
    el.append(name);

    const activate = () => (selecting ? toggleSelected(item.id) : openViewer(item.id));
    el.addEventListener("click", activate);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        activate();
      }
    });
    return el;
  }

  function renderGallery() {
    $("gallery").replaceChildren(...ordered().map((item) => tile(item, false)));
  }

  function removeItems(ids) {
    const gone = new Set(ids);
    state.items = state.items.filter((i) => !gone.has(i.id));
    for (const id of ids) {
      selected.delete(id);
      const el = $("gallery").querySelector(`.tile[data-id="${id}"]`);
      if (el) {
        el.classList.add("removing");
        setTimeout(() => el.remove(), 280);
      }
    }
    renderStats();
    renderSelection();
    if (viewer.open && gone.has(viewer.id)) viewerAfterRemoval();
    else if (viewer.open) renderStrip();
  }

  // ---- selección ------------------------------------------------------------------

  function setSelecting(on) {
    selecting = on;
    if (!on) selected.clear();
    document.body.classList.toggle("is-selecting", on);
    for (const el of $("gallery").children) el.classList.toggle("selected", selected.has(Number(el.dataset.id)));
    renderSelection();
    $("gallery-hint").hidden = state.items.length === 0 || on;
  }

  function toggleSelected(id) {
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    const el = $("gallery").querySelector(`.tile[data-id="${id}"]`);
    if (el) el.classList.toggle("selected", selected.has(id));
    renderSelection();
  }

  function renderSelection() {
    $("selection-bar").hidden = !selecting;
    const n = selected.size;
    $("selection-count").textContent = n ? plural(n, "seleccionado", "seleccionados") : "Elige fotos";
    $("delete-selected").disabled = n === 0;
    const all = n === state.items.length && n > 0;
    $("select-all").textContent = all ? "Quitar selección" : "Seleccionar todo";
  }

  // ---- borrar ----------------------------------------------------------------------

  let pendingDelete = [];

  function confirmDelete(ids) {
    if (!ids.length) return;
    pendingDelete = ids;
    const item = ids.length === 1 ? itemById(ids[0]) : null;
    $("confirm-title").textContent = item ? `¿Borrar «${item.name}»?` : `¿Borrar ${ids.length} archivos?`;
    $("confirm-modal").hidden = false;
    $("confirm-ok").focus();
  }

  async function doDelete() {
    const ids = pendingDelete;
    pendingDelete = [];
    $("confirm-modal").hidden = true;
    const { ok, data } = await action("delete", { ids });
    const removed = data.removed || [];
    if (removed.length) {
      removeItems(removed);
      toast(`${plural(removed.length, "archivo movido", "archivos movidos")} a la Papelera`);
    }
    if (!ok || removed.length < ids.length) toast("No se pudieron borrar algunos archivos", "error");
    if (selecting && selected.size === 0) setSelecting(false);
  }

  // ---- visor -------------------------------------------------------------------------

  function openViewer(id) {
    viewer.open = true;
    viewer.id = id;
    $("viewer").hidden = false;
    renderViewer();
    renderStrip();
  }

  function closeViewer() {
    viewer.open = false;
    $("viewer").hidden = true;
    $("viewer-stage").replaceChildren(); // detiene el vídeo
  }

  function step(delta) {
    const list = ordered();
    const index = list.findIndex((i) => i.id === viewer.id);
    const next = list[index + delta];
    if (next) {
      viewer.id = next.id;
      renderViewer();
      renderStrip();
    }
  }

  function viewerAfterRemoval() {
    const list = ordered();
    if (!list.length) return closeViewer();
    // el siguiente en la lista ocupa el lugar del borrado
    const strip = [...$("viewer-strip").children].map((b) => Number(b.dataset.id));
    const oldIndex = strip.indexOf(viewer.id);
    const candidate = strip.slice(oldIndex + 1).concat(strip.slice(0, oldIndex).reverse()).find((id) => itemById(id));
    viewer.id = candidate ?? list[0].id;
    renderViewer();
    renderStrip();
  }

  function fallback(item, message) {
    const box = document.createElement("div");
    box.className = "viewer-fallback";
    box.innerHTML = `${item.kind === "video" ? ICONS.video : ICONS.image}<b></b><span></span>`;
    box.querySelector("b").textContent = item.name;
    box.querySelector("span").textContent = message;
    const button = document.createElement("button");
    button.className = "btn btn-primary";
    button.type = "button";
    button.textContent = "Abrir con otra aplicación";
    button.addEventListener("click", () => action("open", { id: item.id }));
    box.append(button);
    return box;
  }

  function renderViewer() {
    const item = itemById(viewer.id);
    if (!item) return closeViewer();
    const list = ordered();
    const index = list.findIndex((i) => i.id === item.id);

    $("viewer-name").textContent = item.name;
    $("viewer-meta").textContent = [
      `${index + 1} de ${list.length}`,
      formatSize(item.size),
      formatDate(item.taken),
    ]
      .filter(Boolean)
      .join(" · ");
    $("viewer-prev").disabled = index <= 0;
    $("viewer-next").disabled = index >= list.length - 1;

    const stage = $("viewer-stage");
    stage.classList.remove("zoomed");
    const spinner = document.createElement("span");
    spinner.className = "spinner";

    if (item.view === "video") {
      const video = document.createElement("video");
      video.controls = true;
      video.autoplay = true;
      video.playsInline = true;
      video.src = withKey(`/desktop/file/${item.id}`);
      video.onerror = () =>
        stage.replaceChildren(fallback(item, "Este vídeo usa un formato que la ventana no puede reproducir."));
      stage.replaceChildren(video);
      return;
    }

    const img = new Image();
    img.alt = item.name;
    img.decoding = "async";
    img.src = withKey(item.view === "image" ? `/desktop/file/${item.id}` : `/desktop/preview/${item.id}`);
    img.onload = () => {
      if (viewer.id === item.id) stage.replaceChildren(img);
    };
    img.onerror = () => {
      if (viewer.id === item.id)
        stage.replaceChildren(fallback(item, "Windows no sabe mostrar este formato. Puedes abrirlo con otra aplicación."));
    };
    img.addEventListener("click", (e) => toggleZoom(e, img));
    stage.replaceChildren(spinner);
  }

  function toggleZoom(event, img) {
    const stage = $("viewer-stage");
    if (stage.classList.contains("zoomed")) {
      stage.classList.remove("zoomed");
      return;
    }
    const rect = img.getBoundingClientRect();
    const fx = (event.clientX - rect.left) / rect.width;
    const fy = (event.clientY - rect.top) / rect.height;
    stage.classList.add("zoomed");
    stage.scrollLeft = img.naturalWidth * fx - stage.clientWidth / 2;
    stage.scrollTop = img.naturalHeight * fy - stage.clientHeight / 2;
  }

  function renderStrip() {
    const strip = $("viewer-strip");
    strip.replaceChildren(
      ...ordered().map((item) => {
        const b = document.createElement("button");
        b.type = "button";
        b.dataset.id = item.id;
        b.title = item.name;
        b.className = item.id === viewer.id ? "current" : "";
        const img = document.createElement("img");
        img.alt = "";
        img.loading = "lazy";
        img.src = withKey(`/desktop/thumb/${item.id}`);
        img.onerror = () => {
          const s = document.createElement("span");
          s.textContent = item.ext;
          img.replaceWith(s);
        };
        b.append(img);
        b.addEventListener("click", () => {
          viewer.id = item.id;
          renderViewer();
          renderStrip();
        });
        return b;
      })
    );
    strip.querySelector(".current")?.scrollIntoView({ block: "nearest", inline: "center" });
  }

  // ---- actualización -------------------------------------------------------------------

  function renderUpdate() {
    const update = state.update;
    $("update-banner").hidden = !update;
    if (!update) return;
    $("update-text").textContent = `Hay una versión nueva de PhotoBridge: v${update.version}`;
    $("update-button").disabled = update.busy;
  }

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

  function renderAll() {
    $("version").textContent = state.version ? `v${state.version}` : "";
    renderView();
    renderQr();
    renderFolder();
    renderStats();
    renderTransfers();
    renderGallery();
    renderUpdate();
    renderSelection();
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
      if (viewer.open) renderStrip();
    }
    renderView();
    renderStats();
    renderTransfers();
    renderSelection();
  });

  events.addEventListener("removed", (e) => {
    const ids = JSON.parse(e.data).ids.filter((id) => itemById(id));
    if (ids.length) removeItems(ids);
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

  // ---- botones y teclado ------------------------------------------------------------

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

  $("select-toggle").addEventListener("click", () => setSelecting(!selecting));
  $("select-cancel").addEventListener("click", () => setSelecting(false));
  $("select-all").addEventListener("click", () => {
    const all = selected.size === state.items.length;
    selected.clear();
    if (!all) state.items.forEach((i) => selected.add(i.id));
    for (const el of $("gallery").children) el.classList.toggle("selected", selected.has(Number(el.dataset.id)));
    renderSelection();
  });
  $("delete-selected").addEventListener("click", () => confirmDelete([...selected]));
  $("confirm-ok").addEventListener("click", doDelete);
  $("confirm-cancel").addEventListener("click", () => ($("confirm-modal").hidden = true));
  $("confirm-modal").addEventListener("click", (e) => {
    if (e.target === $("confirm-modal")) $("confirm-modal").hidden = true;
  });

  $("viewer-close").addEventListener("click", closeViewer);
  $("viewer-prev").addEventListener("click", () => step(-1));
  $("viewer-next").addEventListener("click", () => step(1));
  $("viewer-delete").addEventListener("click", () => confirmDelete([viewer.id]));
  $("viewer-external").addEventListener("click", () => action("open", { id: viewer.id }));
  $("viewer-reveal").addEventListener("click", () => action("reveal", { id: viewer.id }));
  $("viewer-stage").addEventListener("click", (e) => {
    if (e.target === $("viewer-stage")) closeViewer();
  });

  document.addEventListener("keydown", (e) => {
    if (!$("confirm-modal").hidden) {
      if (e.key === "Escape") $("confirm-modal").hidden = true;
      if (e.key === "Enter") doDelete();
      return;
    }
    if (viewer.open) {
      if (e.key === "Escape") closeViewer();
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "ArrowRight") step(1);
      else if (e.key === "Delete") confirmDelete([viewer.id]);
      return;
    }
    if (e.key === "Escape") {
      $("qr-modal").hidden = true;
      $("update-modal").hidden = true;
      if (selecting) setSelecting(false);
    } else if (e.key === "Delete" && selecting && selected.size) {
      confirmDelete([...selected]);
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && state.items.length) {
      e.preventDefault();
      setSelecting(true);
      state.items.forEach((i) => selected.add(i.id));
      for (const el of $("gallery").children) el.classList.add("selected");
      renderSelection();
    }
  });

  // Es una app, no una web: sin menú contextual ni zoom del navegador
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("wheel", (e) => e.ctrlKey && e.preventDefault(), { passive: false });
})();
