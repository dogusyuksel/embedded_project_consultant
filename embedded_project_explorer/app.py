from flask import Flask, jsonify, render_template, request
from pathlib import Path
import json

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = (BASE_DIR / ".." / "enriched_project_graph.json").resolve()
AI_CONTEXT_DIR = (BASE_DIR / ".." / "ai_context").resolve()

app = Flask(__name__)

def load_raw():
    if not DATA_FILE.exists():
        return {"_error": f"Graph not found: {DATA_FILE}"}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": str(exc)}

def iter_functions(raw):
    if raw.get("files"):
        for file_info in raw.get("files", []):
            for function in file_info.get("functions", []):
                yield function
    else:
        yield from raw.get("functions", [])

def function_id(function):
    return str(function.get("id") or function.get("refid") or function.get("name"))

def find_function(raw, symbol_id):
    symbol_id = str(symbol_id or "")
    for function in iter_functions(raw):
        if function_id(function) == symbol_id or function.get("name") == symbol_id:
            return function
    return None

def source_root(raw):
    root = raw.get("project", {}).get("source_root") or "."
    return Path(root)

def read_function_source(raw, function):
    location = function.get("location", {}) or {}
    file_name = location.get("file") or function.get("file")
    start = location.get("body_start") or location.get("line")
    end = location.get("body_end") or start

    if not file_name or not start:
        return ""

    path = source_root(raw) / file_name

    if not path.exists():
        return ""

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[start - 1 : end])

def related_functions(raw, symbol_id):
    functions_by_id = {
        function_id(function): function
        for function in iter_functions(raw)
    }
    incoming = []
    outgoing = []

    for edge in raw.get("edges", []):
        source = str(edge.get("source") or edge.get("from") or "")
        target = str(edge.get("target") or edge.get("to") or "")

        if target == str(symbol_id) and source in functions_by_id:
            incoming.append(functions_by_id[source])

        if source == str(symbol_id) and target in functions_by_id:
            outgoing.append(functions_by_id[target])

    return incoming, outgoing

def build_ai_context(raw, symbol_id, question):
    selected = find_function(raw, symbol_id)

    if not selected:
        return "# AI Query Context\n\nSelected symbol not found.\n"

    incoming, outgoing = related_functions(raw, function_id(selected))

    def section_for_function(title, function):
        source = read_function_source(raw, function)
        return "\n".join([
            f"## {title}: {function.get('name')}",
            "",
            f"- id: {function_id(function)}",
            f"- file: {(function.get('location') or {}).get('file') or function.get('file')}",
            f"- classification: {function.get('classification')}",
            "",
            "```c",
            source,
            "```",
            "",
        ])

    parts = [
        "# AI Query Context",
        "",
        "## Question",
        "",
        question or "",
        "",
        section_for_function("Selected node", selected),
        "## Called by",
        "",
    ]

    if incoming:
        for function in incoming:
            parts.append(section_for_function("Caller", function))
    else:
        parts.append("No callers found.\n")

    parts.extend(["", "## Calls", ""])

    if outgoing:
        for function in outgoing:
            parts.append(section_for_function("Callee", function))
    else:
        parts.append("No callees found.\n")

    return "\n".join(parts)

def write_ai_context_file(raw, symbol_id, question):
    AI_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    path = AI_CONTEXT_DIR / "latest_ai_query_context.md"
    path.write_text(
        build_ai_context(raw, symbol_id, question),
        encoding="utf-8",
    )
    return path

def normalize_graph(raw):
    """
    Supports the actual analyzer format:
      project/statistics/files[].functions[]
    while also accepting the older top-level nodes/edges format.
    """
    if "_error" in raw:
        return {"nodes": [], "edges": [], "error": raw["_error"], "statistics": {}}

    # Actual enriched_project_graph format.
    files = raw.get("files", [])
    top_level_functions = raw.get("functions", [])
    if files or top_level_functions:
        nodes = []
        node_ids = set()

        function_items = []

        if files:
            for f in files:
                for fn in f.get("functions", []):
                    function_items.append((fn, f.get("name")))
        else:
            function_items = [(fn, None) for fn in top_level_functions]

        for fn, file_name in function_items:
            loc = fn.get("location", {}) or {}
            doc = fn.get("documentation", {}) or {}
            embedded = fn.get("embedded", {}) or {}

            d = dict(fn)
            d["id"] = str(fn.get("id") or fn.get("refid") or fn.get("name"))
            d["type"] = str(
                fn.get("classification")
                or embedded.get("type")
                or fn.get("kind")
                or "FUNCTION"
            ).upper().replace("-", "_").replace(" ", "_")
            d["file"] = fn.get("file") or file_name or loc.get("file")
            d["location"] = loc
            d["documentation"] = doc
            d["embedded"] = embedded
            # Keep convenient fields for frontend compatibility.
            d["brief"] = doc.get("brief", "")
            d["detail"] = doc.get("detail", "")
            nodes.append(d)
            node_ids.add(d["id"])

        # Doxygen parser may have references/referenced_by but sparse/absent edge list.
        edges = []
        seen = set()

        def add_edge(source, target, kind="CALL"):
            source, target = str(source), str(target)
            if source not in node_ids or target not in node_ids or source == target:
                return
            key = (source, target, kind)
            if key in seen:
                return
            seen.add(key)
            edges.append({
                "id": f"edge_{len(edges)}",
                "source": source,
                "target": target,
                "type": kind
            })

        for fn in nodes:
            for ref in fn.get("references", []) or []:
                add_edge(fn["id"], ref.get("refid"), "CALLS")
            for ref in fn.get("referenced_by", []) or []:
                add_edge(ref.get("refid"), fn["id"], "CALLS")

        # Preserve analyzer/top-level edges too.
        for e in raw.get("edges", []) or []:
            ed = e.get("data", e)
            add_edge(ed.get("source") or ed.get("from"),
                     ed.get("target") or ed.get("to"),
                     ed.get("type") or ed.get("kind") or "CALLS")

        return {
            "nodes": nodes,
            "edges": edges,
            "statistics": raw.get("statistics", {}),
            "project": raw.get("project", {}),
            "error": None
        }

    # Backward-compatible old format.
    nodes = [n.get("data", n) for n in raw.get("nodes", [])]
    edges = [e.get("data", e) for e in raw.get("edges", [])]
    return {
        "nodes": nodes,
        "edges": edges,
        "statistics": raw.get("statistics", {}),
        "project": raw.get("project", {}),
        "error": None
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/project")
def project():
    return jsonify(normalize_graph(load_raw()))

@app.route("/api/config")
def config():
    return jsonify({"graph_path": str(DATA_FILE), "exists": DATA_FILE.exists()})

@app.route("/api/source/<symbol_id>")
def source(symbol_id):
    raw = load_raw()

    if "_error" in raw:
        return jsonify({"error": raw["_error"]}), 404

    function = find_function(raw, symbol_id)

    if not function:
        return jsonify({"error": "symbol not found"}), 404

    return jsonify({
        "id": function_id(function),
        "name": function.get("name"),
        "source": read_function_source(raw, function),
        "location": function.get("location", {}),
    })

@app.route("/api/overview")
def overview():
    graph = normalize_graph(load_raw())

    if graph.get("error"):
        return jsonify(graph), 404

    counts = {}

    for node in graph.get("nodes", []):
        counts[node.get("type")] = counts.get(node.get("type"), 0) + 1

    return jsonify({
        "statistics": graph.get("statistics", {}),
        "counts": counts,
        "entry_points": [
            node
            for node in graph.get("nodes", [])
            if node.get("type") == "ENTRY_POINT"
        ],
        "tasks": [
            node
            for node in graph.get("nodes", [])
            if node.get("type") == "RTOS_TASK"
        ],
        "interrupts": [
            node
            for node in graph.get("nodes", [])
            if node.get("type") == "ISR"
        ],
        "callbacks": [
            node
            for node in graph.get("nodes", [])
            if node.get("type") == "CALLBACK"
        ],
    })

@app.route("/api/ask", methods=["POST"])
def ask_ai():
    payload = request.get_json(silent=True) or {}
    raw = load_raw()
    context_file = None

    if "_error" not in raw:
        context_file = write_ai_context_file(
            raw,
            payload.get("symbol_id"),
            payload.get("question"),
        )

    context = {
        "symbol_id": payload.get("symbol_id"),
        "symbol_name": payload.get("symbol_name"),
        "question": payload.get("question"),
    }
    return jsonify({
        "status": "not_implemented",
        "message": "Ask AI context file was prepared. AI answering is not implemented yet.",
        "received_context": context,
        "context_file": str(context_file) if context_file else None,
    }), 501

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
