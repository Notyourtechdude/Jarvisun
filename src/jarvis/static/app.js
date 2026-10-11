(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  let token = "";
  let connected = false;
  let generation = 0;
  let focusSession = null;
  let chatPending = false;
  let capabilities = {};
  let focusSnapshotAt = 0;
  let pendingTool = null;
  let pendingChat = null;
  const busyButtons = new Set();
  const requests = new Set();
  let voice = {transcription: false, synthesis: false};
  const audioUrls = new Map();
  const toolDefinitions = [
    {name: "list_files", label: "List workspace files", capability: "files", description: "List files in a workspace folder.", arguments: {path: "."}},
    {name: "read_file", label: "Read a file", capability: "files", description: "Read an existing workspace file.", arguments: {path: "notes.md"}},
    {name: "write_file", label: "Write a file", capability: "files", description: "Create or replace a workspace file with the supplied content.", arguments: {path: "notes.md", content: ""}},
    {name: "calculate", label: "Calculate", capability: "calculator", description: "Evaluate restricted arithmetic; optionally supply a unit.", arguments: {expression: "(24 * 3) + 12"}},
    {name: "vault_search", label: "Search the vault", capability: "vault", description: "Find passages in your indexed documents.", arguments: {query: ""}},
    {name: "business_report", label: "Business report", capability: "files", description: "Build a report from a workspace CSV file.", arguments: {path: "sales.csv"}},
    {name: "create_website", label: "Create a website", capability: "files", description: "Generate a local website file for your business.", arguments: {path: "website.html", business: "Your business", title: "Welcome"}},
    {name: "edit_video", label: "Trim a video", capability: "video", description: "Trim a workspace video using the configured local video engine.", arguments: {input: "input.mp4", output: "clip.mp4", start: 0, duration: 10}}
  ];
  const text = (tag, content, className) => {
    const element = document.createElement(tag);
    element.textContent = String(content ?? "");
    if (className) element.className = className;
    return element;
  };
  const showError = (message) => {
    byId("notice").textContent = message;
    byId("notice").hidden = false;
  };
  const clearError = () => { byId("notice").hidden = true; byId("notice").textContent = ""; };
  const available = (name) => capabilities[name] === true || capabilities[name]?.available === true;
  function syncControls() {
    document.querySelectorAll("[data-auth]").forEach((button) => { button.disabled = !connected; });
    ["index-vault", "load-graph"].forEach((id) => { byId(id).disabled = !connected || !available("vault"); });
    byId("vault-form").querySelector("button").disabled = !connected || !available("vault");
    byId("tool-form").querySelector("button").disabled = !connected || !byId("tool-name").value;
    byId("chat-form").querySelector("button").disabled = !connected || chatPending;
    byId("voice-file").disabled = !connected || !voice.transcription || chatPending;
    byId("voice-form").querySelector("button").disabled = !connected || !voice.transcription || chatPending;
    byId("workflow-form").querySelector("button").disabled = !connected || !byId("tool-name").value;
    document.querySelectorAll("[data-speech]").forEach((button) => { button.disabled = !connected || !voice.synthesis; });
    busyButtons.forEach((button) => { button.disabled = true; });
    byId("chat-hint").textContent = chatPending ? "Working…" : connected ? "Ctrl / ⌘ + Enter to send. Check sources before acting." : "Connect to begin. Sources and verification appear with each answer.";
  }
  function disconnect(message) {
    token = "";
    connected = false;
    generation += 1;
    requests.forEach((request) => request.abort());
    requests.clear();
    capabilities = {};
    voice = {transcription: false, synthesis: false};
    byId("voice-file").value = "";
    byId("voice-state").textContent = "Connect to check local voice engines.";
    byId("voice-summary").textContent = "· optional";
    byId("voice-controls").open = false;
    byId("workflow-name").value = "";
    byId("workflow-controls").open = false;
    byId("workflow-tool").textContent = "Choose a workspace tool above to schedule it.";
    byId("workflow-feedback").textContent = "Refresh to see the service's scheduled workflows.";
    byId("workflows").replaceChildren();
    focusSession = null;
    pendingTool = null;
    pendingChat = null;
    chatPending = false;
    byId("token").value = "";
    byId("message").value = "";
    byId("focus-task").value = "";
    byId("vault-query").value = "";
    byId("tool-arguments").value = "{}";
    byId("connection-state").textContent = "Disconnected";
    byId("connection-state").classList.remove("connected");
    byId("disconnect").hidden = true;
    byId("connection-panel").hidden = false;
    byId("connected-overview").hidden = true;
    byId("focus-form").hidden = false;
    byId("focus-session").hidden = true;
    byId("focus-progress").textContent = "Connect to see today's progress.";
    byId("focus-next-action").textContent = "";
    byId("focus-clock").textContent = "";
    byId("focus-state").textContent = "";
    byId("vault-roots").textContent = "Connect to view configured folders.";
    byId("vault-feedback").textContent = "Only configured folders are indexed.";
    byId("workspace-path").textContent = "Connect to view your workspace.";
    byId("version").textContent = "";
    byId("model-status").textContent = "";
    byId("vault-status").textContent = "";
    resetChat();
    byId("vault-results").replaceChildren(text("p", "Search to see matching passages and their exact sources.", "empty-copy"));
    byId("vault-graph").replaceChildren(text("p", "Load the graph after indexing your documents.", "empty-copy"));
    byId("graph-sources").replaceChildren();
    byId("jobs").replaceChildren(text("p", "Connect and refresh to view jobs.", "empty-copy"));
    byId("audit").replaceChildren(text("p", "Connect and refresh to view audit events.", "empty-copy"));
    byId("capabilities").replaceChildren();
    byId("service-warnings").replaceChildren();
    byId("service-notes").hidden = true;
    byId("tool-name").replaceChildren(text("option", "Choose an available tool"));
    byId("tool-name").firstChild.value = "";
    byId("tool-description").textContent = "Available tools appear after connecting.";
    byId("tool-output").textContent = "Run a tool to see its result.";
    clearError();
    if (message) showError(message);
    syncControls();
  }
  async function api(path, options = {}) {
    if (!token) throw new Error("Connect with your owner token first.");
    const request = new AbortController();
    const currentGeneration = generation;
    requests.add(request);
    const timeout = window.setTimeout(() => request.abort(), 120000);
    try {
      const response = await fetch(path, {...options, signal: request.signal, cache: "no-store", headers: {"Authorization": `Bearer ${token}`, "Content-Type": "application/json"}});
      if (currentGeneration !== generation) throw new DOMException("Session ended", "AbortError");
      if (response.status === 401) {
        disconnect("Your access token was rejected or expired. Connect again to continue.");
        byId("token").focus();
        throw new DOMException("Session ended", "AbortError");
      }
      let body;
      try { body = await response.json(); } catch { throw new Error("The service returned an unreadable response. Try again."); }
      if (currentGeneration !== generation) throw new DOMException("Session ended", "AbortError");
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`);
      return body;
    } catch (error) {
      if (error.name === "AbortError" && currentGeneration === generation) throw new Error("The request timed out. Check the service and retry; activity may show whether an action completed.");
      if (error instanceof TypeError) throw new Error("Could not reach Jarvis. Check that the service is running, then try again.");
      throw error;
    } finally {
      window.clearTimeout(timeout);
      requests.delete(request);
    }
  }
  const requestKey = () => globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const post = (path, body) => api(path, {method: "POST", body: JSON.stringify(body)});
  async function run(button, work) {
    if (button.disabled) return;
    const before = button.textContent;
    busyButtons.add(button);
    button.disabled = true;
    button.textContent = "Working…";
    clearError();
    try { await work(); } catch (error) { if (error.name !== "AbortError") showError(error.message); }
    finally {
      if (button.textContent === "Working…") button.textContent = before;
      busyButtons.delete(button);
      button.disabled = false;
      syncControls();
    }
  }
  function renderStatus(status) {
    capabilities = status.capabilities || {};
    const voiceStatus = status.voice || capabilities.voice || {};
    const ready = (value) => value === true || value?.available === true;
    voice = {transcription: ready(voiceStatus.transcription), synthesis: ready(voiceStatus.synthesis)};
    byId("voice-summary").textContent = voice.transcription || voice.synthesis ? "· local engines available" : "· unavailable";
    byId("voice-state").textContent = `Transcription: ${voice.transcription ? "available" : "unavailable"}. Speech: ${voice.synthesis ? "available" : "unavailable"}.${voiceStatus.reason ? ` ${voiceStatus.reason}` : ""}`;
    byId("connection-state").textContent = "Connected";
    byId("connection-state").classList.add("connected");
    byId("disconnect").hidden = false;
    byId("connection-panel").hidden = true;
    byId("connected-overview").hidden = false;
    byId("version").textContent = status.version ? `v${status.version}` : "";
    byId("model-status").textContent = status.model?.available ? `Model: ${status.model.model || "available"}` : "Local model unavailable · retrieval & tools remain available";
    const roots = status.vault?.roots || [];
    byId("vault-status").textContent = `${roots.length} configured vault folder${roots.length === 1 ? "" : "s"}`;
    byId("vault-roots").textContent = roots.length ? `Configured folders: ${roots.join(" · ")}` : "No vault folders configured. Set authorized roots in your service configuration.";
    byId("workspace-path").textContent = status.workspace ? `Workspace: ${status.workspace}` : "Workspace path unavailable.";
    const entries = Object.entries(capabilities);
    byId("capabilities").replaceChildren(...entries.map(([name, value]) => {
      const row = document.createElement("div");
      const ready = value === true || value?.available === true;
      row.append(text("dt", name.replaceAll("_", " ")), text("dd", ready ? "Available" : `Unavailable${value?.reason ? ` · ${value.reason}` : ""}`));
      return row;
    }));
    byId("workflow-tool").textContent = "Choose a workspace tool above to schedule it.";
    const select = byId("tool-name");
    select.replaceChildren(text("option", "Choose an available tool"));
    select.firstChild.value = "";
    toolDefinitions.filter((tool) => available(tool.capability)).forEach((tool) => {
      const option = text("option", tool.label); option.value = tool.name; select.append(option);
    });
    const warnings = Array.isArray(status.warnings) ? status.warnings : [];
    byId("service-notes").hidden = !warnings.length;
    byId("service-warnings").replaceChildren(...warnings.map((warning) => text("li", warning)));
    syncControls();
  }
  byId("connect-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    await run(button, async () => {
      token = byId("token").value.trim();
      byId("token").value = "";
      try {
        const status = await api("/api/status");
        connected = true;
        renderStatus(status);
        byId("message").focus();
        await refreshFocus();
      } catch (error) {
        if (!connected && error.name !== "AbortError") disconnect(error.message);
        else throw error;
      }
    });
  });
  byId("disconnect").addEventListener("click", () => { disconnect(); byId("token").focus(); });
  byId("refresh-status").addEventListener("click", (event) => run(event.currentTarget, async () => { renderStatus(await api("/api/status")); await refreshFocus(); }));
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll("[data-view]").forEach((nav) => { nav.classList.toggle("selected", nav === button); nav.setAttribute("aria-pressed", String(nav === button)); });
    document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== `view-${button.dataset.view}`; });
    byId("current-section").textContent = button.textContent.trim().replace(/^[^A-Za-z]+/, "");
    const heading = byId(`view-${button.dataset.view}`).querySelector("h2");
    heading.tabIndex = -1;
    heading.focus();
  }));
  const emptyChat = byId("chat-empty");
  function releaseAudio(audio) {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    const url = audioUrls.get(audio);
    if (url) URL.revokeObjectURL(url);
    audioUrls.delete(audio);
  }
  function resetChat() {
    [...audioUrls.keys()].forEach(releaseAudio);
    byId("chat-log").replaceChildren(emptyChat);
  }
  byId("clear-chat").addEventListener("click", resetChat);
  document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => { byId("message").value = button.dataset.prompt; byId("message").focus(); }));
  function addMessage(role, content, response) {
    emptyChat.remove();
    const article = document.createElement("article");
    article.className = `message ${role}`;
    article.append(text("p", role === "owner" ? "You" : "Jarvis", "message-label"), text("p", content, "message-content"));
    if (response) {
      const verification = response.kind === "evidence" ? "Source excerpts · factual truth not independently verified" : response.verified ? "Verified result" : "Unverified · check before acting";
      const labels = [verification, response.kind || "response"];
      if (response.note) labels.push(response.note);
      if (response.usage?.total_tokens != null) labels.push(`${response.usage.total_tokens} tokens`);
      article.append(text("p", labels.join(" · "), "message-meta"));
      if (Array.isArray(response.sources) && response.sources.length) {
        const details = document.createElement("details"); details.className = "sources";
        details.append(text("summary", `${response.sources.length} source${response.sources.length === 1 ? "" : "s"}`));
        const list = document.createElement("ul");
        response.sources.forEach((source) => {
          const label = typeof source === "string" ? source : [source.path || source.source || source.title || "Document", source.chunk_id != null ? `passage ${source.chunk_id}` : ""].filter(Boolean).join(" · ");
          const item = text("li", label);
          if (typeof source === "object" && source.text) item.append(text("p", source.text));
          list.append(item);
        });
        details.append(list); article.append(details);
      }
      if (voice.synthesis) {
        const speak = text("button", String(content).length > 1000 ? "Listen to first 1,000 characters" : "Listen");
        speak.type = "button";
        speak.className = "speech-button";
        speak.dataset.speech = "true";
        speak.addEventListener("click", () => run(speak, async () => {
          const result = await post("/api/voice/speak", {text: String(content).slice(0, 1000)});
          if (result.mime !== "audio/wav" || typeof result.audio !== "string" || result.audio.length > 7000000) throw new Error("The voice engine returned unsupported audio.");
          let binary;
          try { binary = atob(result.audio); } catch { throw new Error("The voice engine returned unreadable audio."); }
          const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
          if (bytes.length > 5000000) throw new Error("The voice engine returned an oversized recording.");
          const oldAudio = article.querySelector("audio");
          if (oldAudio) { releaseAudio(oldAudio); oldAudio.remove(); }
          const audio = document.createElement("audio");
          audio.controls = true;
          audio.preload = "metadata";
          audio.className = "speech-audio";
          audio.setAttribute("aria-label", "Jarvis response audio");
          const url = URL.createObjectURL(new Blob([bytes], {type: "audio/wav"}));
          audioUrls.set(audio, url);
          audio.src = url;
          article.append(audio);
          binary = "";
          try { await audio.play(); } catch { /* Native controls remain available if autoplay is blocked. */ }
        }));
        article.append(speak);
      }
      if (Array.isArray(response.actions) && response.actions.length) {
        const details = document.createElement("details"); details.className = "sources";
        details.append(text("summary", "Action receipts"), text("pre", JSON.stringify(response.actions, null, 2)));
        article.append(details);
      }
    }
    byId("chat-log").append(article);
    byId("chat-log").scrollTop = byId("chat-log").scrollHeight;
    return article;
  }
  byId("chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!connected || chatPending) return;
    const message = byId("message").value.trim();
    if (!message) return;
    chatPending = true; syncControls(); clearError();
    addMessage("owner", message); byId("message").value = "";
    if (pendingChat?.message !== message) pendingChat = {message, key: requestKey()};
    try {
      const response = await post("/api/chat", {message, idempotency_key: pendingChat.key});
      pendingChat = null;
      addMessage("assistant", response.answer, response);
    }
    catch (error) { if (error.name !== "AbortError") { showError(error.message); byId("message").value = message; } }
    finally { chatPending = false; syncControls(); if (connected) byId("message").focus(); }
  });
  byId("message").addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); byId("chat-form").requestSubmit(); } });
  function renderFocus(response) {
    focusSession = response.session || null;
    focusSnapshotAt = Date.now();
    const active = focusSession && !["completed", "complete", "cancelled"].includes(focusSession.state || focusSession.status);
    byId("focus-form").hidden = Boolean(active);
    byId("focus-session").hidden = !active;
    byId("focus-progress").textContent = `${response.moved_today || 0} completed focus session${response.moved_today === 1 ? "" : "s"} today. Small steps count.`;
    if (active) {
      byId("focus-next-action").textContent = focusSession.task || focusSession.next_action || "Your current focus";
      const state = focusSession.state || focusSession.status || "active";
      byId("focus-state").textContent = state === "paused" ? "Paused. Resume when you're ready." : "Just this one action. It's okay to stop and reset.";
      byId("focus-toggle").textContent = state === "paused" ? "Resume" : "Pause";
      updateClock();
    }
  }
  function updateClock() {
    if (!focusSession) return;
    const paused = (focusSession.state || focusSession.status) === "paused";
    let remaining = focusSession.remaining_seconds;
    const end = focusSession.ends_at || focusSession.end_at;
    if (end && !paused) remaining = Math.max(0, Math.ceil((Date.parse(end) - Date.now()) / 1000));
    if (remaining == null) remaining = (focusSession.minutes || 15) * 60;
    if (!end && !paused) remaining -= Math.floor((Date.now() - focusSnapshotAt) / 1000);
    if (!Number.isFinite(Number(remaining))) { byId("focus-clock").textContent = "Focus active"; return; }
    remaining = Math.max(0, Math.round(Number(remaining)));
    byId("focus-clock").textContent = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
    if (remaining === 0 && !paused) byId("focus-state").textContent = "Time's up. Mark this session done, or pause for a breath.";
  }
  async function refreshFocus() { renderFocus(await api("/api/focus")); }
  byId("focus-form").addEventListener("submit", (event) => {
    event.preventDefault(); run(event.currentTarget.querySelector("button"), async () => {
      renderFocus(await post("/api/focus", {action: "start", task: byId("focus-task").value.trim(), minutes: Number(byId("focus-minutes").value)}));
    });
  });
  byId("focus-toggle").addEventListener("click", (event) => run(event.currentTarget, async () => { renderFocus(await post("/api/focus", {action: (focusSession?.state || focusSession?.status) === "paused" ? "resume" : "pause"})); }));
  byId("focus-complete").addEventListener("click", (event) => run(event.currentTarget, async () => { renderFocus(await post("/api/focus", {action: "complete"})); byId("focus-task").value = ""; byId("focus-task").focus(); }));
  window.setInterval(updateClock, 1000);
  byId("vault-form").addEventListener("submit", (event) => {
    event.preventDefault(); run(event.currentTarget.querySelector("button"), async () => {
      const query = byId("vault-query").value.trim();
      const response = await api(`/api/vault/search?q=${encodeURIComponent(query)}`);
      const results = response.results || [];
      byId("vault-feedback").textContent = `${results.length} matching passage${results.length === 1 ? "" : "s"}.`;
      byId("vault-results").replaceChildren(...results.map((result) => {
        const article = document.createElement("article"); article.className = "result-item";
        article.append(text("h3", `${result.path || result.source || "Document"}${result.chunk_id != null ? ` · passage ${result.chunk_id}` : ""}`, "result-source"), text("p", result.text, "result-text")); return article;
      }));
      if (!results.length) byId("vault-results").append(text("p", "No matching passages. Try a simpler phrase, or update your index.", "empty-copy"));
    });
  });
  byId("index-vault").addEventListener("click", (event) => run(event.currentTarget, async () => {
    const response = await post("/api/vault/index", {});
    byId("vault-feedback").textContent = `Index update finished: ${JSON.stringify(response)}. Search to inspect the indexed evidence.`;
  }));
  const svgElement = (name, attributes) => {
    const element = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  };
  function renderGraph(response) {
    const nodes = response.nodes || [], edges = response.edges || [];
    byId("vault-graph").replaceChildren(); byId("graph-sources").replaceChildren();
    if (!nodes.length) { byId("vault-graph").append(text("p", "No connections yet. Index your documents to build a source graph.", "empty-copy")); return; }
    const shown = nodes.slice(0, 40);
    const svg = svgElement("svg", {viewBox: "0 0 800 480", role: "img", "aria-label": `Source graph: ${nodes.length} nodes, ${edges.length} relationships. Text sources are listed below.`});
    const points = new Map();
    shown.forEach((node, i) => { const angle = (i / shown.length) * Math.PI * 2 - Math.PI / 2; points.set(String(node.id ?? i), {x: 400 + Math.cos(angle) * 290, y: 240 + Math.sin(angle) * 180}); });
    edges.forEach((edge) => {
      const from = points.get(String(edge.source ?? edge.from)), to = points.get(String(edge.target ?? edge.to));
      if (from && to) svg.append(svgElement("line", {x1: from.x, y1: from.y, x2: to.x, y2: to.y, stroke: "#52604f", "stroke-width": 1}));
    });
    shown.forEach((node, i) => {
      const point = points.get(String(node.id ?? i));
      const label = String(node.label || node.path || node.title || node.source || node.id || "Source");
      const group = svgElement("g", {}), title = svgElement("title", {}); title.textContent = label;
      group.append(title, svgElement("circle", {cx: point.x, cy: point.y, r: 5, fill: "#c3d2ad"}));
      const caption = svgElement("text", {x: point.x, y: point.y + 20, fill: "#f0f0e8", "text-anchor": "middle", "font-size": 10});
      const short = label.split(/[\\/]/).pop(); caption.textContent = short.length > 24 ? `${short.slice(0, 21)}…` : short;
      group.append(caption); svg.append(group);
    });
    byId("vault-graph").append(svg);
    if (nodes.length > shown.length) byId("vault-graph").append(text("p", `Showing ${shown.length} of ${nodes.length} nodes. All source names are listed below.`, "small muted"));
    nodes.forEach((node) => byId("graph-sources").append(text("li", node.path || node.label || node.title || node.source || node.id)));
  }
  byId("load-graph").addEventListener("click", (event) => run(event.currentTarget, async () => renderGraph(await api("/api/vault/graph"))));
  byId("tool-name").addEventListener("change", () => {
    const definition = toolDefinitions.find((tool) => tool.name === byId("tool-name").value);
    byId("tool-description").textContent = definition?.description || "Choose a tool to see its arguments.";
    byId("tool-arguments").value = JSON.stringify(definition?.arguments || {}, null, 2);
    byId("workflow-tool").textContent = definition ? `Selected tool: ${definition.label}. The current arguments above will be scheduled.` : "Choose a workspace tool above to schedule it.";
    syncControls();
  });
  byId("tool-form").addEventListener("submit", (event) => {
    event.preventDefault(); run(event.currentTarget.querySelector("button"), async () => {
      let args;
      try { args = JSON.parse(byId("tool-arguments").value); } catch { throw new Error("Arguments must be valid JSON. Use double quotes around property names and text."); }
      if (!args || Array.isArray(args) || typeof args !== "object") throw new Error("Arguments must be a JSON object.");
      const tool = byId("tool-name").value;
      const signature = JSON.stringify({tool, arguments: args});
      if (pendingTool?.signature !== signature) {
        const key = requestKey();
        pendingTool = {signature, key};
      }
      const response = await post("/api/tools", {tool, arguments: args, idempotency_key: pendingTool.key});
      byId("tool-output").textContent = JSON.stringify(response, null, 2);
      pendingTool = null;
    });
  });
  function renderActivity(id, records, empty) {
    byId(id).replaceChildren(...records.map((record) => {
      const details = document.createElement("details"); details.className = "activity-item";
      const summary = document.createElement("summary");
      summary.append(text("span", record.tool || record.action || record.event || record.kind || record.type || record.id || "Event"), text("span", [record.state || record.status, record.created_at || record.created || record.timestamp || record.time].filter(Boolean).join(" · ")));
      details.append(summary, text("pre", JSON.stringify(record, null, 2))); return details;
    }));
    if (!records.length) byId(id).append(text("p", empty, "empty-copy"));
  }
  byId("refresh-activity").addEventListener("click", (event) => run(event.currentTarget, async () => {
    const outcomes = await Promise.allSettled([api("/api/jobs"), api("/api/audit")]);
    if (!connected) return;
    if (outcomes[0].status === "fulfilled") renderActivity("jobs", outcomes[0].value.jobs || [], "No jobs recorded yet.");
    if (outcomes[1].status === "fulfilled") renderActivity("audit", outcomes[1].value.events || [], "No audit events recorded yet.");
    const failure = outcomes.find((outcome) => outcome.status === "rejected");
    if (failure) throw failure.reason;
  }));
  function validateRecording(buffer) {
    const view = new DataView(buffer);
    const label = (offset, length) => String.fromCharCode(...new Uint8Array(buffer, offset, length));
    if (view.byteLength < 44 || label(0, 4) !== "RIFF" || label(8, 4) !== "WAVE") throw new Error("Choose a valid WAV recording.");
    let format = null, dataLength = 0;
    for (let offset = 12; offset + 8 <= view.byteLength;) {
      const size = view.getUint32(offset + 4, true);
      if (offset + 8 + size > view.byteLength) throw new Error("The WAV recording is incomplete.");
      const chunk = label(offset, 4);
      if (chunk === "fmt " && size >= 16) format = {encoding: view.getUint16(offset + 8, true), channels: view.getUint16(offset + 10, true), rate: view.getUint32(offset + 12, true), bits: view.getUint16(offset + 22, true)};
      if (chunk === "data") dataLength += size;
      offset += 8 + size + (size % 2);
    }
    if (!format || format.encoding !== 1 || format.channels !== 1 || format.rate !== 16000 || format.bits !== 16) throw new Error("Use a 16 kHz, mono, 16-bit PCM WAV recording.");
    if (!dataLength || dataLength % 2 || dataLength > 16000 * 2 * 30) throw new Error("The recording must contain audio and be at most 30 seconds long.");
  }
  byId("voice-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run(event.currentTarget.querySelector("button"), async () => {
      const file = byId("voice-file").files[0];
      if (!file) throw new Error("Choose a WAV recording first.");
      if (file.size > 3000000) throw new Error("Choose a recording smaller than 3 MB and up to 30 seconds long.");
      const sessionGeneration = generation;
      let buffer = await file.arrayBuffer();
      if (sessionGeneration !== generation) throw new DOMException("Session ended", "AbortError");
      validateRecording(buffer);
      let bytes = new Uint8Array(buffer), binary = "", audio = "";
      for (let index = 0; index < bytes.length; index += 8192) binary += String.fromCharCode(...bytes.subarray(index, index + 8192));
      audio = btoa(binary);
      binary = "";
      bytes = null;
      buffer = null;
      chatPending = true;
      syncControls();
      byId("voice-state").textContent = "Transcribing your recording locally…";
      try {
        const response = await post("/api/voice/chat", {audio});
        addMessage("owner", response.transcript || response.transcription || "Voice recording");
        addMessage("assistant", response.answer, response);
        byId("voice-state").textContent = "Recording processed. Sources and verification appear with the answer.";
        byId("voice-file").value = "";
      } catch (error) {
        if (connected) byId("voice-state").textContent = "Recording was not processed. Check the error and try again.";
        throw error;
      } finally {
        audio = "";
        chatPending = false;
        syncControls();
      }
    });
  });
  function workflowArguments() {
    let args;
    try { args = JSON.parse(byId("tool-arguments").value); } catch { throw new Error("Tool arguments must be valid JSON before scheduling."); }
    if (!args || Array.isArray(args) || typeof args !== "object") throw new Error("Tool arguments must be a JSON object.");
    return args;
  }
  function renderWorkflows(workflows) {
    byId("workflows").replaceChildren(...workflows.map((workflow) => {
      const row = document.createElement("article"); row.className = "workflow-row";
      const heading = document.createElement("div"); heading.className = "workflow-row-heading";
      const toggle = text("button", workflow.paused ? "Resume" : "Pause"); toggle.type = "button"; toggle.dataset.auth = "";
      toggle.setAttribute("aria-label", `${workflow.paused ? "Resume" : "Pause"} workflow ${workflow.name}`);
      toggle.addEventListener("click", () => run(toggle, async () => {
        await post(`/api/workflows/${encodeURIComponent(workflow.id)}/pause`, {paused: !Boolean(workflow.paused)});
        await loadWorkflows();
      }));
      heading.append(text("h4", workflow.name), toggle);
      const next = Number(workflow.next_run);
      const nextLabel = Number.isFinite(next) && next > 0 ? new Date(next * 1000).toLocaleString() : "not scheduled";
      row.append(heading, text("p", `${workflow.tool} · every ${workflow.interval} seconds · ${workflow.paused ? "Paused" : `Active · next run ${nextLabel}`}`, "small muted"));
      if (workflow.error || workflow.last_error) row.append(text("p", `Last run error: ${workflow.error || workflow.last_error}`, "small"));
      const details = document.createElement("details");
      details.append(text("summary", "Arguments & schedule receipt"), text("pre", JSON.stringify(workflow, null, 2)));
      row.append(details); return row;
    }));
    if (!workflows.length) byId("workflows").append(text("p", "No recurring workflows scheduled.", "empty-copy"));
    syncControls();
  }
  async function loadWorkflows() {
    try {
      const response = await api("/api/workflows");
      renderWorkflows(response.workflows || []);
      byId("workflow-feedback").textContent = "Schedule state refreshed from the service. Runs appear in Activity.";
    } catch (error) {
      if (connected) byId("workflow-feedback").textContent = "Could not refresh workflows. Check the error and retry.";
      throw error;
    }
  }
  byId("refresh-workflows").addEventListener("click", (event) => run(event.currentTarget, loadWorkflows));
  byId("workflow-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run(event.currentTarget.querySelector("button"), async () => {
      const name = byId("workflow-name").value.trim();
      const tool = byId("tool-name").value;
      const interval = Number(byId("workflow-interval").value);
      if (!name || !tool || !Number.isInteger(interval) || interval < 60 || interval > 2592000) throw new Error("Choose a tool, name the workflow, and use an interval of 60 seconds to 30 days.");
      await post("/api/workflows", {name, tool, arguments: workflowArguments(), interval});
      byId("workflow-feedback").textContent = "Workflow scheduled. Refresh the list to check its state.";
      await loadWorkflows();
    });
  });
  window.addEventListener("pagehide", () => disconnect());
  syncControls();
})();
