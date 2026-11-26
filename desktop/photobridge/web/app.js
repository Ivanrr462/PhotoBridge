(() => {
  "use strict";

  // ---- ajustes del envío -----------------------------------------------------
  const CHUNK_SIZE = 8 * 1024 * 1024; // trozos de 8 MB: si algo falla solo se repite un trozo
  const PARALLEL = 3; // archivos a la vez (aprovecha mejor la wifi con muchas fotos)
  const MAX_ERRORS = 8; // fallos seguidos permitidos por archivo antes de rendirse
  const SAMPLE = 64 * 1024; // bytes del principio, medio y final para identificar el archivo
  const RING = 2 * Math.PI * 18;

  const token = new URLSearchParams(location.search).get("t") || "";
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const $ = (id) => document.getElementById(id);

  const ICONS = {
    video:
      '<svg viewBox="0 0 24 24"><path d="m16 13 5.2 3.5a.5.5 0 0 0 .8-.4V7.9a.5.5 0 0 0-.8-.4L16 11"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>',
    image:
      '<svg viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/></svg>',
    play: '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>',
    equal: '<svg viewBox="0 0 24 24"><path d="M5 9h14"/><path d="M5 15h14"/></svg>',
    alert: '<svg viewBox="0 0 24 24"><path d="M12 7v6"/><path d="M12 17h.01"/></svg>',
  };

  let selected = []; // [{ file, tile, url }]
  let sending = false;

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

  function formatDuration(seconds) {
    if (!isFinite(seconds) || seconds <= 0) return "";
    if (seconds < 60) return "menos de 1 min";
    const minutes = Math.round(seconds / 60);
    return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
  }

  function clock(seconds) {
    if (!isFinite(seconds)) return "";
    const s = Math.round(seconds);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  function isVideo(file) {
    return file.type.startsWith("video/") || /\.(mov|mp4|m4v|3gp|avi|mkv|webm)$/i.test(file.name);
  }

  function setStep(step) {
    document.body.dataset.step = step;
    $("step-pick").hidden = step !== "pick";
    $("step-grid").hidden = step === "pick";
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // Si el móvil bloquea la pantalla, esperamos a que vuelva antes de reintentar.
  function whenVisible() {
    if (document.visibilityState === "visible") return Promise.resolve();
    return new Promise((resolve) => {
      const onChange = () => {
        if (document.visibilityState === "visible") {
          document.removeEventListener("visibilitychange", onChange);
          resolve();
        }
      };
      document.addEventListener("visibilitychange", onChange);
    });
  }

  // ---- conexión con el ordenador ------------------------------------------

  async function loadInfo() {
    try {
      const res = await fetch("/api/info", { headers: { "X-Token": token } });
      if (!res.ok) throw new Error(String(res.status));
      const info = await res.json();
      $("computer").textContent = info.computer;
      $("computer").title = `Conectado a ${info.computer}`;
      $("computer").className = "status ok";
    } catch {
      $("computer").textContent = "Sin conexión con el ordenador";
      $("computer").className = "status error";
    }
  }

  // ---- miniaturas (solo las que se ven, para no gastar memoria) ----------------

  const visible = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const entryItem = selected.find((s) => s.tile === entry.target);
        if (!entryItem) continue;
        if (entry.isIntersecting) showPreview(entryItem);
        else hidePreview(entryItem);
      }
    },
    { rootMargin: "600px 0px" }
  );

  function placeholder(file) {
    const ph = document.createElement("span");
    ph.className = "ph";
    ph.innerHTML = isVideo(file) ? ICONS.video : ICONS.image;
    const label = document.createElement("span");
    label.textContent = (file.name.split(".").pop() || "").toUpperCase().slice(0, 5);
    ph.append(label);
    return ph;
  }

  function showPreview(entry) {
    if (entry.url) return;
    const { file, tile } = entry;
    if (isVideo(file)) {
      videoPoster(entry);
      return;
    }
    entry.url = URL.createObjectURL(file);
    const img = document.createElement("img");
    img.alt = "";
    img.decoding = "async";
    img.src = entry.url;
    img.onerror = () => img.replaceWith(placeholder(file));
    tile.querySelector(".media").replaceChildren(img);
  }

  function hidePreview(entry) {
    if (!entry.url || isVideo(entry.file)) return; // los fotogramas de vídeo son ligeros: se quedan
    URL.revokeObjectURL(entry.url);
    entry.url = null;
    entry.tile.querySelector(".media").replaceChildren(placeholder(entry.file));
  }

  // Fotograma del vídeo como miniatura, de uno en uno para no saturar el móvil
  let posterQueue = Promise.resolve();
  function videoPoster(entry) {
    entry.url = "pending";
    posterQueue = posterQueue.then(
      () =>
        new Promise((resolve) => {
          const url = URL.createObjectURL(entry.file);
          const video = document.createElement("video");
          video.muted = true;
          video.playsInline = true;
          video.preload = "metadata";
          const done = () => {
            URL.revokeObjectURL(url);
            resolve();
          };
          const timer = setTimeout(done, 4000);
          video.onloadedmetadata = () => {
            if (isFinite(video.duration)) {
              const tag = entry.tile.querySelector(".duration span");
              if (tag) tag.textContent = clock(video.duration);
            }
            video.currentTime = Math.min(0.5, (video.duration || 1) / 3);
          };
          video.onseeked = () => {
            try {
              const canvas = document.createElement("canvas");
              const scale = 240 / Math.max(video.videoWidth, video.videoHeight, 1);
              canvas.width = Math.round(video.videoWidth * scale);
              canvas.height = Math.round(video.videoHeight * scale);
              canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
              const img = document.createElement("img");
              img.alt = "";
              img.src = canvas.toDataURL("image/jpeg", 0.8);
              entry.tile.querySelector(".media").replaceChildren(img);
            } catch {}
            clearTimeout(timer);
            done();
          };
          video.onerror = () => {
            clearTimeout(timer);
            done();
          };
          video.src = url;
        })
    );
  }

  // ---- cuadrícula --------------------------------------------------------------

  function makeTile(file, index) {
    const tile = document.createElement("div");
    tile.className = "tile";
    tile.style.setProperty("--n", Math.min(index, 30));
    const media = document.createElement("div");
    media.className = "media";
    media.style.cssText = "width:100%;height:100%";
    media.append(placeholder(file));
    tile.append(media);

    if (isVideo(file)) {
      const duration = document.createElement("span");
      duration.className = "duration";
      duration.innerHTML = `${ICONS.play}<span>Vídeo</span>`;
      tile.append(duration);
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove";
    remove.setAttribute("aria-label", `Quitar ${file.name}`);
    remove.innerHTML = ICONS.x;
    remove.addEventListener("click", () => removeFile(tile));
    tile.append(remove);

    const state = document.createElement("div");
    state.className = "state";
    tile.append(state);
    return tile;
  }

  function addFiles(files) {
    const known = new Set(selected.map((s) => `${s.file.name}|${s.file.size}|${s.file.lastModified}`));
    const grid = $("grid");
    for (const file of files) {
      const key = `${file.name}|${file.size}|${file.lastModified}`;
      if (known.has(key)) continue; // la misma foto elegida dos veces
      known.add(key);
      const tile = makeTile(file, selected.length);
      const entry = { file, tile, url: null };
      selected.push(entry);
      grid.append(tile);
      visible.observe(tile);
    }
    renderReview();
  }

  function removeFile(tile) {
    const index = selected.findIndex((s) => s.tile === tile);
    if (index < 0) return;
    const [entry] = selected.splice(index, 1);
    visible.unobserve(tile);
    if (entry.url && entry.url !== "pending") URL.revokeObjectURL(entry.url);
    tile.classList.add("leaving");
    setTimeout(() => tile.remove(), 220);
    if (!selected.length) return reset();
    renderReview();
  }

  function reset() {
    for (const entry of selected) {
      visible.unobserve(entry.tile);
      if (entry.url && entry.url !== "pending") URL.revokeObjectURL(entry.url);
    }
    selected = [];
    $("grid").replaceChildren();
    setStep("pick");
  }

  function renderReview() {
    const size = selected.reduce((sum, s) => sum + s.file.size, 0);
    const videos = selected.filter((s) => isVideo(s.file)).length;
    const photos = selected.length - videos;
    const parts = [];
    if (photos) parts.push(plural(photos, "foto", "fotos"));
    if (videos) parts.push(plural(videos, "vídeo", "vídeos"));
    $("review-title").textContent = parts.join(" y ") || "Nada elegido";
    $("review-size").textContent = `${formatSize(size)} en total · toca × para quitar`;
    $("send-label").textContent = `Enviar ${plural(selected.length, "archivo", "archivos")}`;
    if (document.body.dataset.step !== "review") setStep("review");
  }

  // Estado visual de cada miniatura durante el envío
  function tileState(entry, kind, percent = 0) {
    const { tile } = entry;
    tile.classList.remove("queued", "uploading", "done", "skipped", "failed");
    tile.classList.add(kind);
    const state = tile.querySelector(".state");
    if (kind === "uploading") {
      if (!state.querySelector(".ring")) {
        state.innerHTML =
          '<svg class="ring" viewBox="0 0 44 44"><circle class="track" cx="22" cy="22" r="18"/><circle class="value" cx="22" cy="22" r="18"/></svg><span class="pct"></span>';
      }
      state.querySelector(".value").style.strokeDashoffset = String(RING * (1 - percent / 100));
      state.querySelector(".pct").textContent = `${Math.floor(percent)}%`;
    } else if (kind === "queued") {
      state.replaceChildren();
    } else {
      const icon = kind === "done" ? ICONS.check : kind === "skipped" ? ICONS.equal : ICONS.alert;
      state.innerHTML = `<span class="mark">${icon}</span>` + (kind === "skipped" ? '<span class="tag">Ya estaba</span>' : "");
    }
  }

  // ---- identificador del archivo ----------------------------------------------
  // Se calcula con el tamaño y trozos del contenido. Sirve para retomar un envío cortado
  // y para que el ordenador reconozca fotos que ya tiene (sin depender del nombre).

  function hash64(bytes, seed) {
    let h1 = 0xdeadbeef ^ seed;
    let h2 = 0x41c6ce57 ^ seed;
    for (let i = 0; i < bytes.length; i++) {
      h1 = Math.imul(h1 ^ bytes[i], 2654435761);
      h2 = Math.imul(h2 ^ bytes[i], 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return (h2 >>> 0).toString(16).padStart(8, "0") + (h1 >>> 0).toString(16).padStart(8, "0");
  }

  const ids = new WeakMap();

  async function fileId(file) {
    if (ids.has(file)) return ids.get(file);
    const middle = Math.max(0, Math.floor(file.size / 2) - SAMPLE / 2);
    const parts = [
      new TextEncoder().encode(`${file.size}|`),
      new Uint8Array(await file.slice(0, SAMPLE).arrayBuffer()),
      new Uint8Array(await file.slice(middle, middle + SAMPLE).arrayBuffer()),
      new Uint8Array(await file.slice(Math.max(0, file.size - SAMPLE)).arrayBuffer()),
    ];
    const all = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let pos = 0;
    for (const p of parts) {
      all.set(p, pos);
      pos += p.length;
    }
    const id = hash64(all, 1) + hash64(all, 2);
    ids.set(file, id);
    return id;
  }

  // ---- protocolo con el ordenador ---------------------------------------------

  function apiError(status, code) {
    const err = new Error(code || `HTTP ${status}`);
    err.code = code;
    err.status = status;
    err.fatal = status === 401 || status === 507 || status === 400;
    return err;
  }

  async function startUpload(id, file) {
    let res;
    try {
      res = await fetch("/api/uploads", {
        method: "POST",
        headers: { "X-Token": token, "Content-Type": "application/json" },
        body: JSON.stringify({ id, name: file.name || "foto", size: file.size, lastModified: file.lastModified || 0 }),
      });
    } catch {
      throw apiError(0, "network");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw apiError(res.status, data.error);
    return data;
  }

  function putChunk(id, offset, blob, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", `/api/uploads/${id}?offset=${offset}`);
      xhr.setRequestHeader("X-Token", token);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.timeout = 120000;
      xhr.upload.onprogress = (e) => onProgress(e.loaded);
      xhr.onload = () => {
        let data = {};
        try {
          data = JSON.parse(xhr.responseText);
        } catch {}
        if (xhr.status === 200) return resolve(data);
        if (xhr.status === 409) return resolve({ offset: data.offset, done: false }); // reajuste
        reject(apiError(xhr.status, data.error));
      };
      xhr.onerror = () => reject(apiError(0, "network"));
      xhr.ontimeout = () => reject(apiError(0, "timeout"));
      xhr.onabort = () => reject(apiError(0, "abort"));
      xhr.send(blob);
    });
  }

  async function retrying(task, onRetry) {
    let errors = 0;
    for (;;) {
      try {
        return await task();
      } catch (err) {
        if (err.fatal || ++errors > MAX_ERRORS) throw err;
        onRetry?.();
        await whenVisible();
        await wait(Math.min(10000, 750 * 2 ** (errors - 1)));
      }
    }
  }

  async function uploadFile(file, onBytes, onRetry) {
    const id = await fileId(file);
    let state = await retrying(() => startUpload(id, file), onRetry);
    while (!state.done) {
      const base = state.offset;
      const end = Math.min(base + CHUNK_SIZE, file.size);
      state = await retrying(async () => {
        try {
          return await putChunk(id, base, file.slice(base, end), (loaded) => onBytes(base + loaded));
        } catch (err) {
          // Se cortó: preguntamos cuánto llegó de verdad y seguimos desde ahí
          if (!err.fatal) {
            const fresh = await startUpload(id, file).catch(() => null);
            if (fresh && (fresh.done || fresh.offset !== base)) return fresh;
          }
          throw err;
        }
      }, onRetry);
      onBytes(state.offset);
    }
    return state;
  }

  // ---- envío de la selección ----------------------------------------------------

  function speedMeter() {
    let lastBytes = 0;
    let lastTime = performance.now();
    let speed = 0;
    return (bytes) => {
      const now = performance.now();
      const dt = (now - lastTime) / 1000;
      if (dt >= 1) {
        const instant = Math.max(0, bytes - lastBytes) / dt;
        speed = speed ? speed * 0.7 + instant * 0.3 : instant;
        lastBytes = bytes;
        lastTime = now;
      }
      return speed;
    };
  }

  async function sendAll(entries) {
    if (!entries.length) return;
    sending = true;
    setStep("sending");
    entries.forEach((e) => tileState(e, "queued"));

    const files = entries.map((e) => e.file);
    const total = files.reduce((sum, f) => sum + f.size, 0) || 1;
    const loaded = new Array(files.length).fill(0);
    const meter = speedMeter();
    const failed = [];
    let next = 0;
    let completed = 0;
    let skipped = 0;
    let fatal = null;
    let retryingNow = false;

    const update = () => {
      const bytes = loaded.reduce((a, b) => a + b, 0);
      const pct = Math.min(100, (bytes / total) * 100);
      $("progress-percent").textContent = Math.floor(pct);
      $("progress-bar").style.width = `${pct.toFixed(1)}%`;
      $("progress-count").textContent = `${completed} de ${files.length}`;
      const speed = meter(bytes);
      const left = formatDuration((total - bytes) / speed);
      $("progress-speed").textContent =
        speed > 0 ? `${formatSize(speed)}/s${left ? ` · quedan ${left}` : ""}` : "Conectando con el ordenador…";
      $("progress-detail").hidden = !retryingNow;
      $("progress-detail").textContent = "Reconectando… seguirá donde se quedó";
    };
    update();
    const ticker = setInterval(update, 1000);

    const worker = async () => {
      while (!fatal && next < files.length) {
        const i = next++;
        const entry = entries[i];
        tileState(entry, "uploading", 0);
        try {
          const result = await uploadFile(
            files[i],
            (bytes) => {
              loaded[i] = bytes;
              retryingNow = false;
              tileState(entry, "uploading", (bytes / (files[i].size || 1)) * 100);
            },
            () => {
              retryingNow = true;
              update();
            }
          );
          if (result.skipped) skipped++;
          tileState(entry, result.skipped ? "skipped" : "done");
        } catch (err) {
          if (err.fatal) fatal = err;
          failed.push(entry);
          tileState(entry, "failed");
        }
        loaded[i] = files[i].size;
        completed++;
        update();
      }
    };

    await Promise.all(Array.from({ length: Math.min(PARALLEL, files.length) }, worker));
    clearInterval(ticker);
    if (fatal) {
      for (const entry of entries.slice(next)) {
        failed.push(entry);
        tileState(entry, "failed");
      }
    }
    sending = false;
    showResult(entries.length - failed.length, skipped, failed, fatal);
  }

  function showResult(okCount, skipped, failed, fatal) {
    setStep("done");
    const hasErrors = failed.length > 0;
    const sent = okCount - skipped;
    $("done-icon").classList.toggle("has-errors", hasErrors);
    $("done-title").textContent = hasErrors ? (okCount > 0 ? "Casi listo" : "No se pudo enviar") : "¡Listo!";

    let message = hasErrors ? "Algunos archivos no han llegado." : "Ya están en tu ordenador.";
    if (fatal?.code === "no_space") message = "El ordenador se ha quedado sin espacio en el disco.";
    else if (fatal?.code === "token") message = "El enlace ya no es válido: vuelve a escanear el QR.";
    $("done-message").textContent = message;

    const stats = [];
    if (sent) stats.push(["", sent, sent === 1 ? "enviado" : "enviados"]);
    if (skipped) stats.push(["", skipped, skipped === 1 ? "ya estaba" : "ya estaban"]);
    if (failed.length) stats.push(["bad", failed.length, failed.length === 1 ? "falló" : "fallaron"]);
    $("done-stats").replaceChildren(
      ...stats.map(([cls, n, label]) => {
        const el = document.createElement("span");
        el.className = cls;
        el.innerHTML = "<b></b> ";
        el.firstChild.textContent = n;
        el.append(label);
        return el;
      })
    );

    $("retry-button").hidden = !hasErrors || fatal?.code === "token";
    retryList = failed;
    if (!hasErrors) {
      celebrate();
      navigator.vibrate?.([30, 40, 30]);
    }
  }

  let retryList = [];

  function celebrate() {
    const colors = ["#4353ff", "#19c3fb", "#12b76a", "#ffb38a", "#c2b5ff", "#ff7eb3", "#ffd27a"];
    const box = $("confetti");
    box.replaceChildren(
      ...Array.from({ length: 60 }, () => {
        const piece = document.createElement("i");
        piece.style.left = `${Math.random() * 100}%`;
        piece.style.background = colors[Math.floor(Math.random() * colors.length)];
        piece.style.setProperty("--x", `${(Math.random() - 0.5) * 160}px`);
        piece.style.setProperty("--r", `${360 + Math.random() * 720}deg`);
        piece.style.setProperty("--t", `${2 + Math.random() * 1.6}s`);
        piece.style.animationDelay = `${Math.random() * 0.4}s`;
        return piece;
      })
    );
    setTimeout(() => box.replaceChildren(), 4500);
  }

  // ---- eventos ----------------------------------------------------------------

  function onPicked(e) {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (files.length) addFiles(files);
  }

  $("file-input").addEventListener("change", onPicked);
  $("file-input-files").addEventListener("change", onPicked);
  $("add-more").addEventListener("click", () => $("file-input").click());
  $("send-button").addEventListener("click", () => sendAll(selected));
  $("retry-button").addEventListener("click", () => sendAll(retryList));
  $("repick-button").addEventListener("click", reset);
  $("more-button").addEventListener("click", reset);

  window.addEventListener("beforeunload", (e) => {
    if (sending) e.preventDefault();
  });

  if (isIOS) $("ios-tips").hidden = false;
  loadInfo();
})();
