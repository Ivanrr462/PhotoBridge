(() => {
  "use strict";

  // ---- ajustes del envío -----------------------------------------------------
  const CHUNK_SIZE = 8 * 1024 * 1024; // trozos de 8 MB: si algo falla solo se repite un trozo
  const PARALLEL = 3; // archivos a la vez (aprovecha mejor la wifi con muchas fotos)
  const MAX_ERRORS = 8; // fallos seguidos permitidos por archivo antes de rendirse
  const SAMPLE = 64 * 1024; // bytes del principio, medio y final para identificar el archivo

  const token = new URLSearchParams(location.search).get("t") || "";
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  const $ = (id) => document.getElementById(id);
  const steps = ["step-pick", "step-confirm", "step-sending", "step-done"];

  let selected = [];
  let sending = false;

  function show(step) {
    for (const id of steps) $(id).hidden = id !== step;
  }

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

  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

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

  // ---- vista previa ---------------------------------------------------------

  const PREVIEW_SLOTS = 8;
  let previewUrls = [];

  function renderPreview(files) {
    previewUrls.forEach((url) => URL.revokeObjectURL(url));
    previewUrls = [];
    const showMore = files.length > PREVIEW_SLOTS;
    const visible = files.slice(0, showMore ? PREVIEW_SLOTS - 1 : PREVIEW_SLOTS);
    const tiles = visible.map((file) => {
      const tile = document.createElement("div");
      tile.className = "thumb";
      // Safari (iPhone) sabe mostrar HEIC; si un navegador no puede, se pone un icono
      if (file.type.startsWith("image/") || /\.(heic|heif)$/i.test(file.name)) {
        const img = document.createElement("img");
        const url = URL.createObjectURL(file);
        previewUrls.push(url);
        img.src = url;
        img.alt = "";
        img.decoding = "async";
        img.onerror = () => img.replaceWith(placeholderIcon(file));
        tile.append(img);
      } else {
        tile.append(placeholderIcon(file));
      }
      if (isVideo(file)) tile.classList.add("is-video");
      return tile;
    });
    if (showMore) {
      const more = document.createElement("div");
      more.className = "thumb more";
      more.textContent = `+${files.length - visible.length}`;
      tiles.push(more);
    }
    $("preview").replaceChildren(...tiles);
  }

  function isVideo(file) {
    return file.type.startsWith("video/") || /\.(mov|mp4|m4v|3gp|avi|mkv)$/i.test(file.name);
  }

  function placeholderIcon(file) {
    const span = document.createElement("span");
    span.innerHTML = isVideo(file)
      ? '<svg viewBox="0 0 24 24"><path d="m16 13 5.2 3.5a.5.5 0 0 0 .8-.4V7.9a.5.5 0 0 0-.8-.4L16 11"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>'
      : '<svg viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/></svg>';
    return span;
  }

  // ---- progreso -----------------------------------------------------------

  const RING_LENGTH = 2 * Math.PI * 52;

  function setProgress(pct) {
    $("progress-bar").style.width = pct.toFixed(1) + "%";
    $("ring-value").style.strokeDashoffset = String(RING_LENGTH * (1 - pct / 100));
    $("progress-percent").textContent = `${Math.floor(pct)} %`;
  }

  // Velocidad media suavizada para mostrar MB/s y el tiempo restante
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

  async function sendAll(files) {
    if (!files.length) return;
    sending = true;
    show("step-sending");
    setProgress(0);

    const total = files.reduce((sum, f) => sum + f.size, 0) || 1;
    const loaded = new Array(files.length).fill(0);
    const meter = speedMeter();
    const failed = [];
    const active = new Set();
    let next = 0;
    let completed = 0;
    let skipped = 0;
    let fatal = null;
    let retryingNow = false;

    const update = () => {
      const bytes = loaded.reduce((a, b) => a + b, 0);
      setProgress(Math.min(100, (bytes / total) * 100));
      $("progress-count").textContent = `${Math.min(completed + 1, files.length)} de ${files.length}`;
      const current = [...active].map((i) => files[i].name).join(", ");
      $("progress-detail").textContent = retryingNow ? "Reconectando… seguirá donde se quedó" : current;
      const speed = meter(bytes);
      const left = formatDuration((total - bytes) / speed);
      $("progress-speed").textContent = speed > 0 ? `${formatSize(speed)}/s${left ? ` · quedan ${left}` : ""}` : "";
    };
    const ticker = setInterval(update, 1000);

    const worker = async () => {
      while (!fatal && next < files.length) {
        const i = next++;
        active.add(i);
        update();
        try {
          const result = await uploadFile(
            files[i],
            (bytes) => {
              loaded[i] = bytes;
              retryingNow = false;
            },
            () => {
              retryingNow = true;
              update();
            }
          );
          if (result.skipped) skipped++;
        } catch (err) {
          if (err.fatal) fatal = err;
          failed.push(files[i]);
        }
        loaded[i] = files[i].size;
        completed++;
        active.delete(i);
        update();
      }
    };

    await Promise.all(Array.from({ length: Math.min(PARALLEL, files.length) }, worker));
    clearInterval(ticker);
    if (fatal) failed.push(...files.slice(next));
    sending = false;
    showResult(files.length - failed.length, skipped, failed, fatal);
  }

  function showResult(okCount, skipped, failed, fatal) {
    show("step-done");
    const hasErrors = failed.length > 0;
    $("done-icon").classList.toggle("has-errors", hasErrors);
    $("done-title").textContent = hasErrors ? (okCount > 0 ? "Casi listo" : "No se pudo enviar") : "¡Listo!";

    const sent = okCount - skipped;
    let message = "";
    if (sent > 0) message += `${plural(sent, "archivo enviado", "archivos enviados")} a tu ordenador.`;
    if (skipped > 0) {
      message += skipped === 1
        ? " 1 ya estaba en el ordenador, así que no se ha duplicado."
        : ` ${skipped} ya estaban en el ordenador, así que no se han duplicado.`;
    }
    if (fatal?.code === "no_space") message += " El ordenador se ha quedado sin espacio en el disco.";
    else if (fatal?.code === "token") message += " El enlace ya no es válido: vuelve a escanear el QR.";
    else if (hasErrors) message += ` ${plural(failed.length, "no se pudo enviar", "no se pudieron enviar")}.`;
    $("done-message").textContent = message.trim();

    const list = $("failed-list");
    list.replaceChildren(
      ...failed.slice(0, 50).map((f) => {
        const li = document.createElement("li");
        li.textContent = f.name;
        return li;
      })
    );
    list.hidden = !hasErrors;
    $("retry-button").hidden = !hasErrors || fatal?.code === "token";
    selected = failed;
  }

  // ---- eventos ----------------------------------------------------------------

  function onPicked(e) {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    selected = files;
    const size = selected.reduce((sum, f) => sum + f.size, 0);
    $("selected-count").textContent = plural(selected.length, "archivo listo", "archivos listos");
    $("selected-size").textContent = `${formatSize(size)} en total`;
    renderPreview(selected);
    show("step-confirm");
  }

  $("file-input").addEventListener("change", onPicked);
  $("file-input-files").addEventListener("change", onPicked);
  $("send-button").addEventListener("click", () => sendAll(selected));
  $("retry-button").addEventListener("click", () => sendAll(selected));
  $("repick-button").addEventListener("click", () => $("file-input").click());
  $("more-button").addEventListener("click", () => {
    selected = [];
    show("step-pick");
  });

  window.addEventListener("beforeunload", (e) => {
    if (sending) e.preventDefault();
  });

  if (isIOS) $("ios-tips").hidden = false;
  loadInfo();
})();
