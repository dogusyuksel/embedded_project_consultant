# Embedded Project Explorer UI

This Flask UI reads `../enriched_project_graph.json` and shows the firmware graph, symbol list, relationships, source snippets, and generated AI context.

Generate the graph from the repository root first:

```bash
python3 doxygen_parser.py doxygen_output/xml project_graph.json \
  --target-project /path/to/firmware \
  --include-folder src \
  --include-folder inc \
  --exclude-folder mx_files \
  --exclude-folder cmake \
  --exclude-folder test \
  --exclude-folder docs
```

Then run the UI:

```bash
cd embedded_project_explorer
python3 -m pip install -r requirements.txt
python3 app.py
```

Open:

```text
http://127.0.0.1:5000
```

Notes:

- Left and right panels are resizable.
- The Source Code tab loads the selected function body.
- The Ask AI tab writes context to `../ai_context/latest_ai_query_context.md`; it does not call an AI model yet.
