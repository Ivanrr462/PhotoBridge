(() => {
  "use strict";

  const MAX_ATTEMPTS = 3;
  const token = new URLSearchParams(location.search).get("t") || "";

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

  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
  }

  // ---- conexión con el ordenador ------------------------------------------

  async function loadInfo() {
    try {
      const res = await fetch("/api/info", { headers: { "X-Token": token } });
      if (!res.ok) throw new Error(String(res.status));
      const info = await res.json();
      $("computer").textContent = info.computer;
      $("computer").title = `Conectado a ${info.computer}`;
      $("computer").classList.add("ok");
    } catch {
      $("computer").textContent = "Sin conexión con el ordenador";
      $("computer").classList.add("error");
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
      if (file.type.startsWith("image/") && !/hei[cf]/i.test(file.type)) {
        const img = document.createElement("img");
        const url = URL.createObjectURL(file);
        previewUrls.push(url);
        img.src = url;
        img.alt = "";
        img.loading = "lazy";
        img.onerror = () => img.replaceWith(placeholderIcon(file));
        tile.append(img);
      } else {
        tile.append(placeholderIcon(file));
      }
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

  function placeholderIcon(file) {
    const isVideo = file.type.startsWith("video/");
    const span = document.createElement("span");
    span.innerHTML = isVideo
      ? '<svg viewBox="0 0 24 24"><path d="m16 13 5.2 3.5a.5.5 0 0 0 .8-.4V7.9a.5.5 0 0 0-.8-.4L16 11"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>'
      : '<svg viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/></svg>';
    return span;
  }

  const RING_LENGTH = 2 * Math.PI * 52;

  function setProgress(pct) {
    $("progress-bar").style.width = pct.toFixed(1) + "%";
    $("ring-value").style.strokeDashoffset = String(RING_LENGTH * (1 - pct / 100));
    $("progress-percent").textContent = `${Math.floor(pct)} %`;
  }

  // ---- envío ------------------------------------------------------------------

  function uploadFile(file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload");
      xhr.setRequestHeader("X-Token", token);
      xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name || "foto"));
      xhr.setRequestHeader("X-Last-Modified", String(file.lastModified || 0));
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (e) => onProgress(e.loaded);
      xhr.onload = () => {
        if (xhr.status === 200) return resolve();
        let code = "";
        try {
          code = JSON.parse(xhr.responseText).error;
        } catch {}
        const err = new Error(code || `HTTP ${xhr.status}`);
        err.fatal = xhr.status === 401 || xhr.status === 507;
        err.code = code;
        reject(err);
      };
      xhr.onerror = () => reject(new Error("network"));
      xhr.onabort = () => reject(new Error("abort"));
      xhr.send(file);
    });
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  async function sendAll(files) {
    sending = true;
    show("step-sending");

    const total = files.reduce((sum, f) => sum + f.size, 0) || 1;
    let doneBytes = 0;
    const failed = [];
    setProgress(0);
    let fatal = null;

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      $("progress-count").textContent = `${i + 1} de ${files.length}`;
      $("progress-detail").textContent = file.name;

      let ok = false;
      for (let attempt = 1; attempt <= MAX_ATTEMPTS && !ok; attempt++) {
        try {
          await uploadFile(file, (loaded) => {
            setProgress(Math.min(100, ((doneBytes + loaded) / total) * 100));
          });
          ok = true;
        } catch (err) {
          if (err.fatal) {
            fatal = err;
            break;
          }
          if (attempt < MAX_ATTEMPTS) await wait(1000 * attempt);
        }
      }

      doneBytes += file.size;
      if (!ok) failed.push(file);
      if (fatal) {
        failed.push(...files.slice(i + 1));
        break;
      }
    }

    sending = false;
    showResult(files.length - failed.length, failed, fatal);
  }

  function showResult(okCount, failed, fatal) {
    show("step-done");
    const hasErrors = failed.length > 0;
    $("done-icon").classList.toggle("has-errors", hasErrors);
    $("done-title").textContent = hasErrors ? (okCount > 0 ? "Casi listo" : "No se pudo enviar") : "¡Listo!";

    let message = okCount > 0
      ? `${plural(okCount, "archivo enviado", "archivos enviados")} a tu ordenador.`
      : "";
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

  $("file-input").addEventListener("change", (e) => {
    selected = Array.from(e.target.files || []);
    e.target.value = "";
    if (!selected.length) return;
    const size = selected.reduce((sum, f) => sum + f.size, 0);
    $("selected-count").textContent = plural(selected.length, "archivo listo", "archivos listos");
    $("selected-size").textContent = `${formatSize(size)} en total`;
    renderPreview(selected);
    show("step-confirm");
  });

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

  loadInfo();
})();
