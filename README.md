# Embedded Project Explorer

Embedded Project Explorer turns an embedded C/C++ firmware repository into a navigable software graph. It uses Doxygen XML as the structured source, enriches it with embedded-specific analysis, and serves the result in a lightweight web UI.

The current scope is focused on STM32/FreeRTOS-style projects:

- functions, files, defines, documentation, and call edges
- RTOS task creation through `xTaskCreate`
- task notification edges through `xTaskNotify` / `xTaskNotifyFromISR`
- ISR and HAL callback classification
- source snippets for selected functions
- AI context file generation without making an AI API call

## Generate The Graph

Run from WSL:

```bash
cd /mnt/c/cygwin64/home/dodo-/embedded_peoject_consultant

TARGET=/mnt/c/cygwin64/home/dodo-/stm32f1-master-example/bootloader_firmware

python3 doxygen_parser.py doxygen_output/xml project_graph.json \
  --target-project "$TARGET" \
  --include-folder src \
  --include-folder inc \
  --exclude-folder mx_files \
  --exclude-folder cmake \
  --exclude-folder test \
  --exclude-folder docs
```

This single command:

- copies this repo's `Doxyfile` into the target as `.embedded_explorer.Doxyfile`
- overrides `INPUT`, `EXCLUDE`, XML/source options from the CLI
- runs Doxygen inside the target project
- copies `doxygen_output/` back into this repo
- writes `project_graph.json`
- writes `enriched_project_graph.json`

## Run The UI

```bash
cd /mnt/c/cygwin64/home/dodo-/embedded_peoject_consultant/embedded_project_explorer
python3 -m pip install -r requirements.txt
python3 app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Ask AI Context

The UI does not call an AI model yet. When you use the Ask AI tab, it writes the selected node, the question, callers, callees, and related source snippets to:

```text
ai_context/latest_ai_query_context.md
```

## Tests

```bash
cd /mnt/c/cygwin64/home/dodo-/embedded_peoject_consultant
python3 -B -m unittest tests.test_embedded_pipeline
```
