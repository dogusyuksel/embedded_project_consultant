let graph = { nodes: [], edges: [] };
let cy = null;
let currentFilter = "ALL";
let selectedId = null;
let activeTab = "overview";
let resizeState = null;

const $ = (id) => document.getElementById(id);
const dataOf = (x) => (x && x.data ? x.data : x || {});
const idOf = (d) => String(d.id ?? d.refid ?? d.name ?? "");
const nameOf = (d) => String(d.name ?? d.id ?? "");
const typeOf = (d) =>
  String(d.type ?? d.kind ?? d.classification ?? "FUNCTION")
    .toUpperCase()
    .replaceAll("-", "_")
    .replaceAll(" ", "_");
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      })[c],
  );

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function applySavedPanelSizes() {
  const left = Number(localStorage.getItem("explorer.leftWidth"));
  const right = Number(localStorage.getItem("explorer.rightWidth"));

  if (left) document.documentElement.style.setProperty("--left-width", `${clamp(left, 180, 520)}px`);
  if (right) document.documentElement.style.setProperty("--right-width", `${clamp(right, 280, 720)}px`);
}

function setupResizablePanels() {
  applySavedPanelSizes();

  document.querySelectorAll(".resizeHandle").forEach((handle) => {
    handle.addEventListener("pointerdown", (event) => {
      const styles = getComputedStyle(document.documentElement);
      resizeState = {
        side: handle.dataset.resize,
        startX: event.clientX,
        left: parseInt(styles.getPropertyValue("--left-width"), 10) || 236,
        right: parseInt(styles.getPropertyValue("--right-width"), 10) || 380,
      };
      document.body.classList.add("resizing");
      handle.setPointerCapture(event.pointerId);
    });
  });

  document.addEventListener("pointermove", (event) => {
    if (!resizeState) return;
    const delta = event.clientX - resizeState.startX;

    if (resizeState.side === "left") {
      const width = clamp(resizeState.left + delta, 180, 520);
      document.documentElement.style.setProperty("--left-width", `${width}px`);
      localStorage.setItem("explorer.leftWidth", String(width));
    } else {
      const width = clamp(resizeState.right - delta, 280, 720);
      document.documentElement.style.setProperty("--right-width", `${width}px`);
      localStorage.setItem("explorer.rightWidth", String(width));
    }

    if (cy) cy.resize();
  });

  document.addEventListener("pointerup", () => {
    if (!resizeState) return;
    resizeState = null;
    document.body.classList.remove("resizing");
    if (cy) cy.resize();
  });
}

function nodes() {
  return graph.nodes.map(dataOf);
}

function findNode(id) {
  return nodes().find((d) => idOf(d) === String(id) || nameOf(d) === String(id));
}

function edgeSource(e) {
  const d = dataOf(e);
  return String(d.source ?? d.from ?? "");
}

function edgeTarget(e) {
  const d = dataOf(e);
  return String(d.target ?? d.to ?? "");
}

function edgeType(e) {
  const d = dataOf(e);
  return String(d.type ?? d.kind ?? d.label ?? "CALLS");
}

function embeddedOf(d) {
  const embedded = d.embedded || {};
  return {
    type: embedded.type || typeOf(d),
    rtos: embedded.rtos || {},
    interrupt: embedded.interrupt || {},
    callback: embedded.callback || {},
  };
}

function relations(id) {
  const incoming = [];
  const outgoing = [];
  graph.edges.forEach((e) => {
    const s = edgeSource(e);
    const t = edgeTarget(e);
    if (t === String(id)) {
      const n = findNode(s);
      if (n) incoming.push({ node: n, edge: dataOf(e) });
    }
    if (s === String(id)) {
      const n = findNode(t);
      if (n) outgoing.push({ node: n, edge: dataOf(e) });
    }
  });
  return { incoming, outgoing };
}

function elements() {
  const ns = nodes().map((d) => ({
    group: "nodes",
    data: { id: idOf(d), label: nameOf(d), type: typeOf(d) },
  }));
  const es = graph.edges
    .map((e, i) => ({
      group: "edges",
      data: {
        id: String(dataOf(e).id ?? "e" + i),
        source: edgeSource(e),
        target: edgeTarget(e),
        label: edgeType(e),
      },
    }))
    .filter((e) => e.data.source && e.data.target);
  return [...ns, ...es];
}

function renderGraph() {
  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("cy"),
    elements: elements(),
    minZoom: 0.2,
    maxZoom: 3,
    wheelSensitivity: 0.16,
    style: [
      {
        selector: "node",
        style: {
          label: "data(label)",
          "font-size": 9,
          color: "#dbe4ee",
          "text-wrap": "wrap",
          "text-max-width": 105,
          "text-valign": "center",
          "background-color": "#46576a",
          "border-width": 1,
          "border-color": "#8191a4",
          width: 38,
          height: 38,
        },
      },
      { selector: 'node[type="FUNCTION"]', style: { "background-color": "#40566e", shape: "ellipse" } },
      {
        selector: 'node[type="RTOS_TASK"]',
        style: {
          "background-color": "#3b709f",
          shape: "round-rectangle",
          width: 62,
          height: 40,
          "font-weight": "bold",
        },
      },
      {
        selector: 'node[type="ISR"]',
        style: {
          "background-color": "#895454",
          shape: "diamond",
          width: 48,
          height: 48,
          "font-weight": "bold",
        },
      },
      { selector: 'node[type="CALLBACK"]', style: { "background-color": "#816a3b", shape: "hexagon" } },
      {
        selector: 'node[type="ENTRY_POINT"]',
        style: { "background-color": "#4d7358", width: 48, height: 48, "font-weight": "bold" },
      },
      {
        selector: "edge",
        style: {
          width: 1,
          "line-color": "#536170",
          "target-arrow-color": "#536170",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          label: "data(label)",
          "font-size": 6,
          color: "#738197",
        },
      },
      {
        selector: 'edge[label="REGISTERS_CALLBACK"]',
        style: { "line-color": "#b28a44", "target-arrow-color": "#b28a44", width: 2 },
      },
      {
        selector: 'edge[label="CREATES_RTOS_TASK"]',
        style: { "line-color": "#5da8d6", "target-arrow-color": "#5da8d6", width: 2 },
      },
      { selector: "node:selected", style: { "border-width": 3, "border-color": "#fff" } },
      { selector: ".faded", style: { opacity: 0.13 } },
      { selector: ".highlight", style: { opacity: 1, "border-width": 3, "border-color": "#dce7f5" } },
    ],
  });
  cy.on("tap", "node", (e) => selectNode(e.target.id()));
  cy.on("tap", (e) => {
    if (e.target === cy) clearSelection();
  });
  runLayout();
}

function runLayout() {
  if (!cy) return;
  cy.layout({
    name: "cose",
    animate: false,
    fit: true,
    padding: 55,
    nodeRepulsion: 12000,
    idealEdgeLength: 145,
    edgeElasticity: 70,
    gravity: 0.12,
    componentSpacing: 110,
    nodeOverlap: 30,
    numIter: 1200,
  }).run();
}

function updateCounts() {
  const a = nodes();
  const count = (t) => a.filter((d) => typeOf(d) === t).length;
  $("countAll").textContent = a.length;
  $("countFunction").textContent = count("FUNCTION");
  $("countTask").textContent = count("RTOS_TASK");
  $("countISR").textContent = count("ISR");
  $("countCallback").textContent = count("CALLBACK");
}

function renderResults() {
  const q = $("search").value.trim().toLowerCase();
  const a = nodes()
    .filter((d) => {
      const ok = currentFilter === "ALL" || typeOf(d) === currentFilter;
      const embedded = JSON.stringify(d.embedded || {});
      const text = `${nameOf(d)} ${d.file || ""} ${d.brief || ""} ${d.definition || ""} ${embedded}`.toLowerCase();
      return ok && (!q || text.includes(q));
    })
    .sort((x, y) => nameOf(x).localeCompare(nameOf(y)))
    .slice(0, 150);
  $("results").innerHTML = a.length
    ? a
        .map(
          (d) =>
            `<div class="result" data-id="${esc(idOf(d))}"><div class="resultName">${esc(nameOf(d))}</div><div class="resultType">${esc(typeOf(d))}</div></div>`,
        )
        .join("")
    : `<p class="muted">No matching symbols.</p>`;
  document.querySelectorAll(".result").forEach((el) => (el.onclick = () => selectNode(el.dataset.id)));
}

function relatedBlock(title, items, arrow) {
  if (!items.length) return "";
  return (
    `<div class="relatedTitle">${title}</div>` +
    items
      .map(
        (x) =>
          `<div class="related" data-id="${esc(idOf(x.node))}"><span class="relArrow">${arrow}</span><span class="relName">${esc(nameOf(x.node))}</span><span class="relEdge">${esc(edgeType(x.edge))}</span><span class="relType">${esc(typeOf(x.node))}</span></div>`,
      )
      .join("")
  );
}

function row(k, v) {
  if (v === undefined || v === null || v === "") return "";
  return `<div class="row"><span class="label">${esc(k)}</span><span class="value">${esc(v)}</span></div>`;
}

function overviewHtml(d) {
  const embedded = embeddedOf(d);
  const loc = d.location || {};
  const fields = [
    ["File", d.file],
    ["Line", loc.line],
    ["Definition", d.definition],
    ["Arguments", d.args],
    ["RTOS API", embedded.rtos.api],
    ["Task name", embedded.rtos.name],
    ["Priority", embedded.rtos.priority],
    ["Stack size", embedded.rtos.stack_size],
    ["Created by", embedded.rtos.created_by],
    ["Peripheral", embedded.interrupt.peripheral && `${embedded.interrupt.peripheral.type}/${embedded.interrupt.peripheral.instance}`],
    ["Callback event", embedded.callback.event],
    ["Callback peripheral", embedded.callback.peripheral],
  ];
  return `<div class="card">${fields.map(([k, v]) => row(k, v)).join("") || '<p class="muted">No metadata found.</p>'}</div>`;
}

function documentationHtml(d) {
  const doc = d.documentation || {};
  return `<div class="card">
    ${doc.brief ? `<div class="docBlock"><div class="relatedTitle">BRIEF</div><p>${esc(doc.brief)}</p></div>` : ""}
    ${doc.detail ? `<div class="docBlock"><div class="relatedTitle">DETAIL</div><p>${esc(doc.detail)}</p></div>` : ""}
    ${!doc.brief && !doc.detail ? '<p class="muted">No Doxygen documentation found for this symbol.</p>' : ""}
  </div>`;
}

function sourceHtml(d) {
  const loc = d.location || {};
  return `<div class="card">
    ${row("File", d.file || loc.file)}
    ${row("Body", loc.body_start && loc.body_end ? `${loc.body_start} - ${loc.body_end}` : "")}
    <pre id="sourceCode">Loading source...</pre>
  </div>`;
}

function callbackRegistrationsHtml(d) {
  const regs = embeddedOf(d).callback.registrations || [];
  if (!regs.length) return "";
  return `<div class="card"><div class="relatedTitle">REGISTRATIONS</div>${regs
    .map(
      (r) =>
        `<div class="registration">
          ${row("Registered by", r.registered_by)}
          ${row("API", r.api)}
          ${row("Callback ID", r.callback_id)}
          ${row("Handle", r.handle)}
          ${row("Source", r.source_file && r.source_line ? `${r.source_file}:${r.source_line}` : r.source_file)}
        </div>`,
    )
    .join("")}</div>`;
}

function askHtml(d) {
  return `<div class="card">
    <textarea id="askQuestion" placeholder="Ask about ${esc(nameOf(d))}..."></textarea>
    <button id="askSend" class="primaryBtn">Ask AI</button>
    <div id="askResult" class="muted askResult">AI answering is not implemented yet.</div>
  </div>`;
}

function tabHtml(active) {
  const tabs = [
    ["overview", "Overview"],
    ["docs", "Documentation"],
    ["source", "Source Code"],
    ["ask", "Ask AI"],
  ];
  return `<div class="tabs">${tabs
    .map(([id, label]) => `<button class="tab ${active === id ? "active" : ""}" data-tab="${id}">${label}</button>`)
    .join("")}</div>`;
}

function tabContent(d) {
  if (activeTab === "docs") return documentationHtml(d);
  if (activeTab === "source") return sourceHtml(d);
  if (activeTab === "ask") return askHtml(d);
  return overviewHtml(d) + callbackRegistrationsHtml(d);
}

function showDetails(id) {
  const d = findNode(id);
  if (!d) return;
  const r = relations(id);
  $("details").innerHTML = `<div class="card symbolHead">
      <div class="cardTitle">${esc(nameOf(d))}</div>
      <span class="badge">${esc(typeOf(d))}</span>
    </div>
    ${tabHtml(activeTab)}
    <div id="tabContent">${tabContent(d)}</div>
    <div class="card">${relatedBlock("CALLED BY / INCOMING", r.incoming, "<-")}${relatedBlock("CALLS / OUTGOING", r.outgoing, "->")}${!r.incoming.length && !r.outgoing.length ? '<p class="muted">No relationships found.</p>' : ""}</div>`;

  document.querySelectorAll(".related").forEach((el) => (el.onclick = () => selectNode(el.dataset.id)));
  document.querySelectorAll(".tab").forEach((el) => {
    el.onclick = () => {
      activeTab = el.dataset.tab;
      showDetails(id);
    };
  });
  const askSend = $("askSend");
  if (askSend) askSend.onclick = () => sendAsk(d);
  if (activeTab === "source") loadSource(d);
}

async function loadSource(d) {
  const pre = $("sourceCode");
  if (!pre) return;
  const res = await fetch(`/api/source/${encodeURIComponent(idOf(d))}`);
  const payload = await res.json();
  pre.textContent = payload.source || payload.error || "Source not found.";
}

async function sendAsk(d) {
  const result = $("askResult");
  const question = $("askQuestion").value.trim();
  result.textContent = "Preparing context...";
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, symbol_id: idOf(d), symbol_name: nameOf(d) }),
  });
  const payload = await res.json();
  result.textContent = payload.context_file
    ? `${payload.message} Context: ${payload.context_file}`
    : payload.message || "Ask AI is not implemented yet.";
}

function renderOverviewPanel() {
  const a = nodes();
  const pick = (type) => a.filter((d) => typeOf(d) === type).slice(0, 8);
  const list = (title, items) =>
    `<div class="relatedTitle">${title}</div>` +
    (items.length
      ? items.map((d) => `<div class="related" data-id="${esc(idOf(d))}"><span class="relArrow">#</span><span class="relName">${esc(nameOf(d))}</span><span class="relType">${esc(typeOf(d))}</span></div>`).join("")
      : '<p class="muted">None found.</p>');
  $("details").innerHTML = `<div class="card">
    <div class="cardTitle">Project overview</div>
    <p class="muted">Select a symbol to inspect calls, source, documentation, and AI context.</p>
    ${row("Symbols", a.length)}
    ${row("Relationships", graph.edges.length)}
  </div>
  <div class="card">
    ${list("ENTRY POINTS", pick("ENTRY_POINT"))}
    ${list("RTOS TASKS", pick("RTOS_TASK"))}
    ${list("INTERRUPTS", pick("ISR"))}
    ${list("CALLBACKS", pick("CALLBACK"))}
  </div>`;
  document.querySelectorAll(".related").forEach((el) => (el.onclick = () => selectNode(el.dataset.id)));
}

function selectNode(id) {
  const n = findNode(id);
  if (!n) return;
  selectedId = id;
  activeTab = "overview";
  if (cy) {
    const cn = cy.getElementById(String(id));
    const hood = cn.closedNeighborhood();
    cy.elements().removeClass("highlight faded").unselect();
    cy.elements().addClass("faded");
    hood.removeClass("faded").addClass("highlight");
    cn.select();
    cy.animate({ center: { eles: cn }, zoom: Math.max(cy.zoom(), 1.1) }, { duration: 220 });
  }
  showDetails(id);
}

function clearSelection() {
  selectedId = null;
  if (cy) cy.elements().removeClass("highlight faded").unselect();
  $("details").innerHTML = '<p class="muted">Select a node from the graph.</p>';
}

$("search").addEventListener("input", renderResults);
document.querySelectorAll(".filter").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".filter").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    currentFilter = b.dataset.filter;
    renderResults();
  };
});
$("resetView").onclick = () => {
  if (cy) {
    cy.elements().removeClass("highlight faded").unselect();
    runLayout();
  }
};

setupResizablePanels();

async function init() {
  const res = await fetch("/api/project");
  graph = await res.json();
  if (graph.error) {
    $("projectStats").textContent = "ERROR: " + graph.error;
    return;
  }
  const s = graph.statistics || {};
  $("projectStats").textContent = `${s.files ?? "?"} files | ${s.functions ?? graph.nodes.length} functions | ${s.rtos_tasks ?? 0} tasks | ${s.interrupts ?? 0} ISRs | ${s.callbacks ?? 0} callbacks | ${graph.edges.length} relationships`;
  updateCounts();
  renderGraph();
  renderResults();
  if (selectedId) showDetails(selectedId);
  else renderOverviewPanel();
}

init().catch((e) => {
  console.error(e);
  $("projectStats").textContent = "Could not load project graph.";
});
