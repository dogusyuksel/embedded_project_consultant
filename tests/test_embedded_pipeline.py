import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from doxygen_parser import build_graph, main as parser_main, run_doxygen_pipeline
from embedded_analyzer import analyze_project, remove_comments
import embedded_project_explorer.app as web_app
from embedded_project_explorer.app import app, normalize_graph


class DoxygenParserTests(unittest.TestCase):
    def test_build_graph_filters_files_by_include_and_exclude_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml_dir = Path(tmp) / "xml"
            xml_dir.mkdir()

            (xml_dir / "app_8c.xml").write_text(
                """<?xml version="1.0"?>
<doxygen>
  <compounddef id="app_8c" kind="file" language="C++">
    <compoundname>Src/app.c</compoundname>
    <sectiondef kind="func">
      <memberdef kind="function" id="app_8c_1app_main">
        <type>void</type>
        <definition>void app_main</definition>
        <argsstring>(void)</argsstring>
        <name>app_main</name>
        <location file="Src/app.c" line="10" bodystart="10" bodyend="14"/>
      </memberdef>
    </sectiondef>
  </compounddef>
</doxygen>
""",
                encoding="utf-8",
            )
            (xml_dir / "driver_8c.xml").write_text(
                """<?xml version="1.0"?>
<doxygen>
  <compounddef id="driver_8c" kind="file" language="C++">
    <compoundname>Drivers/vendor.c</compoundname>
    <sectiondef kind="func">
      <memberdef kind="function" id="driver_8c_1vendor_init">
        <type>void</type>
        <definition>void vendor_init</definition>
        <argsstring>(void)</argsstring>
        <name>vendor_init</name>
        <location file="Drivers/vendor.c" line="5" bodystart="5" bodyend="9"/>
      </memberdef>
    </sectiondef>
  </compounddef>
</doxygen>
""",
                encoding="utf-8",
            )

            graph = build_graph(
                xml_dir,
                include_folders=["Src"],
                exclude_folders=["Drivers"],
            )

        self.assertEqual([f["name"] for f in graph["files"]], ["Src/app.c"])
        self.assertEqual([f["name"] for f in graph["functions"]], ["app_main"])

    def test_build_graph_uses_function_location_for_folder_filtering(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml_dir = Path(tmp) / "xml"
            xml_dir.mkdir()

            (xml_dir / "dir.xml").write_text(
                """<?xml version="1.0"?>
<doxygen>
  <compounddef id="dir_src" kind="dir">
    <compoundname>src</compoundname>
  </compounddef>
</doxygen>
""",
                encoding="utf-8",
            )
            (xml_dir / "main_8c.xml").write_text(
                """<?xml version="1.0"?>
<doxygen>
  <compounddef id="main_8c" kind="file" language="C++">
    <compoundname>main.c</compoundname>
    <sectiondef kind="func">
      <memberdef kind="function" id="main_8c_1main">
        <type>int</type>
        <definition>int main</definition>
        <argsstring>(void)</argsstring>
        <name>main</name>
        <location file="src/main.c" line="1" bodystart="2" bodyend="4"/>
      </memberdef>
    </sectiondef>
  </compounddef>
</doxygen>
""",
                encoding="utf-8",
            )

            graph = build_graph(xml_dir, include_folders=["src"])

        self.assertEqual([f["name"] for f in graph["files"]], ["main.c"])
        self.assertEqual([f["name"] for f in graph["functions"]], ["main"])

    def test_build_graph_adds_source_call_edges_when_doxygen_references_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            xml_dir = root / "xml"
            src.mkdir()
            xml_dir.mkdir()
            (src / "main.c").write_text(
                """static void packet_parse_and_respond(void)
{
}

void packet_processor_task(void *data)
{
  packet_parse_and_respond();
}
""",
                encoding="utf-8",
            )
            (xml_dir / "main_8c.xml").write_text(
                """<?xml version="1.0"?>
<doxygen>
  <compounddef id="main_8c" kind="file" language="C++">
    <compoundname>main.c</compoundname>
    <sectiondef kind="func">
      <memberdef kind="function" id="parse">
        <type>void</type>
        <definition>static void packet_parse_and_respond</definition>
        <argsstring>(void)</argsstring>
        <name>packet_parse_and_respond</name>
        <location file="src/main.c" line="1" bodystart="2" bodyend="3"/>
      </memberdef>
      <memberdef kind="function" id="task">
        <type>void</type>
        <definition>void packet_processor_task</definition>
        <argsstring>(void *data)</argsstring>
        <name>packet_processor_task</name>
        <location file="src/main.c" line="5" bodystart="6" bodyend="8"/>
      </memberdef>
    </sectiondef>
  </compounddef>
</doxygen>
""",
                encoding="utf-8",
            )

            graph = build_graph(xml_dir, project_root=root, include_folders=["src"])

        self.assertIn(
            {"source": "task", "target": "parse", "type": "CALLS"},
            graph["edges"],
        )

    def test_build_graph_prefers_definition_over_header_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml_dir = Path(tmp) / "xml"
            xml_dir.mkdir()
            (xml_dir / "api_8h.xml").write_text(
                """<?xml version="1.0"?>
<doxygen><compounddef id="api_8h" kind="file"><compoundname>api.h</compoundname>
<sectiondef kind="func"><memberdef kind="function" id="decl">
<type>void</type><definition>void shared_api</definition><argsstring>(void)</argsstring>
<name>shared_api</name><location file="inc/api.h" line="1"/>
</memberdef></sectiondef></compounddef></doxygen>
""",
                encoding="utf-8",
            )
            (xml_dir / "api_8c.xml").write_text(
                """<?xml version="1.0"?>
<doxygen><compounddef id="api_8c" kind="file"><compoundname>api.c</compoundname>
<sectiondef kind="func"><memberdef kind="function" id="def">
<type>void</type><definition>void shared_api</definition><argsstring>(void)</argsstring>
<name>shared_api</name><location file="src/api.c" line="3" bodystart="4" bodyend="6"/>
</memberdef></sectiondef></compounddef></doxygen>
""",
                encoding="utf-8",
            )

            graph = build_graph(xml_dir, include_folders=["src", "inc"])

        self.assertEqual([f["id"] for f in graph["functions"]], ["def"])

    def test_run_doxygen_pipeline_runs_in_target_and_copies_xml_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "tool"
            target_root = Path(tmp) / "target"
            work_dir.mkdir()
            (work_dir / "Doxyfile").write_text(
                """PROJECT_NAME = Template Project
OUTPUT_DIRECTORY = old_output
INPUT = old_input
EXCLUDE = old_exclude
GENERATE_HTML = YES
GENERATE_XML = NO
CUSTOM_TAG = keep_me
""",
                encoding="utf-8",
            )
            (target_root / "Src").mkdir(parents=True)
            (target_root / "Src" / "app.c").write_text("void app_main(void) {}", encoding="utf-8")

            calls = []

            def fake_runner(command, cwd):
                calls.append((command, Path(cwd)))
                xml_dir = Path(cwd) / "doxygen_output" / "xml"
                xml_dir.mkdir(parents=True)
                (xml_dir / "app_8c.xml").write_text(
                    """<?xml version="1.0"?>
<doxygen>
  <compounddef id="app_8c" kind="file" language="C++">
    <compoundname>Src/app.c</compoundname>
    <sectiondef kind="func">
      <memberdef kind="function" id="app_8c_1app_main">
        <type>void</type>
        <definition>void app_main</definition>
        <argsstring>(void)</argsstring>
        <name>app_main</name>
        <location file="Src/app.c" line="1" bodystart="1" bodyend="1"/>
      </memberdef>
    </sectiondef>
  </compounddef>
</doxygen>
""",
                    encoding="utf-8",
                )

            xml_dir = run_doxygen_pipeline(
                target_project=target_root,
                workspace_root=work_dir,
                include_folders=["Src"],
                exclude_folders=["Drivers"],
                run_command=fake_runner,
            )
            graph = build_graph(xml_dir, include_folders=["Src"])
            copied_xml_exists = (xml_dir / "app_8c.xml").exists()
            generated_doxyfile = (target_root / ".embedded_explorer.Doxyfile").read_text(
                encoding="utf-8",
            )

        self.assertEqual(calls[0][1], target_root)
        self.assertTrue(copied_xml_exists)
        self.assertEqual(xml_dir, work_dir / "doxygen_output" / "xml")
        self.assertEqual([f["name"] for f in graph["functions"]], ["app_main"])
        self.assertIn("CUSTOM_TAG = keep_me", generated_doxyfile)
        self.assertIn("INPUT                  = Src", generated_doxyfile)
        self.assertIn("EXCLUDE                = Drivers", generated_doxyfile)
        self.assertIn("GENERATE_XML           = YES", generated_doxyfile)

    def test_parser_main_writes_project_and_enriched_graph_in_one_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            (target / "src").mkdir()
            (target / "src" / "main.c").write_text("int main(void) { return 0; }", encoding="utf-8")
            xml_dir = root / "xml"
            xml_dir.mkdir()
            (xml_dir / "main_8c.xml").write_text(
                """<?xml version="1.0"?>
<doxygen>
  <compounddef id="main_8c" kind="file" language="C++">
    <compoundname>main.c</compoundname>
    <sectiondef kind="func">
      <memberdef kind="function" id="main">
        <type>int</type>
        <definition>int main</definition>
        <argsstring>(void)</argsstring>
        <name>main</name>
        <location file="src/main.c" line="1" bodystart="1" bodyend="1"/>
      </memberdef>
    </sectiondef>
  </compounddef>
</doxygen>
""",
                encoding="utf-8",
            )
            graph_path = root / "project_graph.json"
            enriched_path = root / "enriched_project_graph.json"

            with contextlib.redirect_stdout(io.StringIO()):
                parser_main([
                    str(xml_dir),
                    str(graph_path),
                    "--project-root",
                    str(target),
                    "--include-folder",
                    "src",
                    "--enriched-output",
                    str(enriched_path),
                ])

            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            enriched = json.loads(enriched_path.read_text(encoding="utf-8"))

        self.assertEqual(graph["statistics"]["functions"], 1)
        self.assertEqual(enriched["statistics"]["functions"], 1)

    def test_parser_main_reads_pipeline_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            (target / "src").mkdir()
            (target / "src" / "main.c").write_text("int main(void) { return 0; }", encoding="utf-8")
            xml_dir = root / "xml"
            xml_dir.mkdir()
            (xml_dir / "main_8c.xml").write_text(
                """<?xml version="1.0"?>
<doxygen>
  <compounddef id="main_8c" kind="file" language="C++">
    <compoundname>main.c</compoundname>
    <sectiondef kind="func">
      <memberdef kind="function" id="main">
        <type>int</type>
        <definition>int main</definition>
        <argsstring>(void)</argsstring>
        <name>main</name>
        <location file="src/main.c" line="1" bodystart="1" bodyend="1"/>
      </memberdef>
    </sectiondef>
  </compounddef>
</doxygen>
""",
                encoding="utf-8",
            )
            config_path = root / "explorer_config.json"
            graph_path = root / "project_graph.json"
            enriched_path = root / "enriched_project_graph.json"
            config_path.write_text(
                json.dumps({
                    "project_root": str(target),
                    "include_folders": ["src"],
                    "exclude_folders": ["test"],
                }),
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                parser_main([
                    "--config",
                    str(config_path),
                    str(xml_dir),
                    str(graph_path),
                    "--enriched-output",
                    str(enriched_path),
                ])

            graph = json.loads(graph_path.read_text(encoding="utf-8"))

        self.assertEqual(graph["project"]["source_root"], str(target))
        self.assertEqual(graph["statistics"]["functions"], 1)


class EmbeddedAnalyzerTests(unittest.TestCase):
    def test_remove_comments_preserves_line_numbers_for_offset_mapping(self):
        source = "int a;\n/* one\n * two\n */\nint b;\n"

        cleaned = remove_comments(source)

        self.assertEqual(cleaned.count("\n"), source.count("\n"))
        self.assertIn("int b;", cleaned)

    def test_rtos_task_creator_uses_location_file_when_compound_file_is_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "main.c").write_text(
                """/*
 * Long file header that used to shift clean-source offsets away from
 * original-source offsets.
 */
int main(void)
{
  xTaskCreate(packet_processor_task, "packet", 512, NULL, 3, NULL);
}

void packet_processor_task(void *argument)
{
}
""",
                encoding="utf-8",
            )
            graph_path = root / "project_graph.json"
            output_path = root / "enriched_project_graph.json"
            graph_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "project": {"name": "fixture"},
                        "statistics": {},
                        "files": [],
                        "functions": [
                            {
                                "id": "main",
                                "name": "main",
                                "file": "main.c",
                                "classification": "ENTRY_POINT",
                                "location": {
                                    "file": "src/main.c",
                                    "line": 5,
                                    "body_start": 6,
                                    "body_end": 8,
                                },
                                "references": [],
                                "referenced_by": [],
                            },
                            {
                                "id": "task",
                                "name": "packet_processor_task",
                                "file": "main.c",
                                "classification": "FUNCTION",
                                "location": {
                                    "file": "src/main.c",
                                    "line": 10,
                                    "body_start": 11,
                                    "body_end": 12,
                                },
                                "references": [],
                                "referenced_by": [],
                            },
                        ],
                        "nodes": [],
                        "edges": [],
                    }
                ),
                encoding="utf-8",
            )

            analyze_project(graph_path, output_path, root)
            enriched = json.loads(output_path.read_text(encoding="utf-8"))

        task = enriched["embedded"]["rtos"]["tasks"][0]
        self.assertEqual(task["created_by"], "main")

    def test_callback_registration_adds_metadata_and_graph_edge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "Src"
            src.mkdir()
            (src / "main.c").write_text(
                """void main(void)
{
  HAL_UART_RegisterCallback(&huart1, HAL_UART_RX_COMPLETE_CB_ID, HAL_UART_RxCpltCallback);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
}
""",
                encoding="utf-8",
            )
            graph_path = root / "project_graph.json"
            output_path = root / "enriched_project_graph.json"
            graph_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "project": {"name": "fixture"},
                        "statistics": {},
                        "files": [],
                        "functions": [
                            {
                                "id": "main",
                                "name": "main",
                                "file": "Src/main.c",
                                "classification": "ENTRY_POINT",
                                "location": {
                                    "file": "Src/main.c",
                                    "line": 1,
                                    "body_start": 2,
                                    "body_end": 4,
                                },
                                "references": [],
                                "referenced_by": [],
                            },
                            {
                                "id": "cb",
                                "name": "HAL_UART_RxCpltCallback",
                                "file": "Src/main.c",
                                "classification": "CALLBACK",
                                "location": {
                                    "file": "Src/main.c",
                                    "line": 6,
                                    "body_start": 7,
                                    "body_end": 8,
                                },
                                "references": [],
                                "referenced_by": [],
                            },
                        ],
                        "nodes": [
                            {
                                "id": "main",
                                "type": "ENTRY_POINT",
                                "name": "main",
                                "data": {"name": "main"},
                            },
                            {
                                "id": "cb",
                                "type": "CALLBACK",
                                "name": "HAL_UART_RxCpltCallback",
                                "data": {"name": "HAL_UART_RxCpltCallback"},
                            },
                        ],
                        "edges": [],
                    }
                ),
                encoding="utf-8",
            )

            analyze_project(graph_path, output_path, root)
            enriched = json.loads(output_path.read_text(encoding="utf-8"))

        registrations = enriched["embedded"]["callbacks"][0].get("registrations", [])
        self.assertEqual(
            registrations,
            [
                {
                    "registered_by": "main",
                    "api": "HAL_UART_RegisterCallback",
                    "handle": "&huart1",
                    "callback_id": "HAL_UART_RX_COMPLETE_CB_ID",
                    "source_file": "Src/main.c",
                    "source_line": 3,
                }
            ],
        )
        self.assertIn(
            {"source": "main", "target": "cb", "type": "REGISTERS_CALLBACK"},
            enriched["edges"],
        )

    def test_task_notify_from_isr_adds_task_notification_edge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "main.c").write_text(
                """void main(void)
{
  xTaskCreate(packet_processor_task, "packet", 512, NULL, 3, &xPacketTask);
}

void USART3_IRQHandler(void)
{
  xTaskNotifyFromISR(xPacketTask, 1, eSetBits, NULL);
}

void packet_processor_task(void *data)
{
}
""",
                encoding="utf-8",
            )
            graph_path = root / "project_graph.json"
            output_path = root / "enriched_project_graph.json"
            graph_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "project": {"name": "fixture"},
                        "statistics": {},
                        "files": [],
                        "functions": [
                            {
                                "id": "main",
                                "name": "main",
                                "file": "main.c",
                                "classification": "ENTRY_POINT",
                                "location": {"file": "src/main.c", "body_start": 2, "body_end": 4},
                                "references": [],
                                "referenced_by": [],
                            },
                            {
                                "id": "isr",
                                "name": "USART3_IRQHandler",
                                "file": "main.c",
                                "classification": "ISR",
                                "location": {"file": "src/main.c", "body_start": 7, "body_end": 9},
                                "references": [],
                                "referenced_by": [],
                            },
                            {
                                "id": "task",
                                "name": "packet_processor_task",
                                "file": "main.c",
                                "classification": "FUNCTION",
                                "location": {"file": "src/main.c", "body_start": 12, "body_end": 13},
                                "references": [],
                                "referenced_by": [],
                            },
                        ],
                        "nodes": [],
                        "edges": [],
                    }
                ),
                encoding="utf-8",
            )

            analyze_project(graph_path, output_path, root)
            enriched = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn(
            {"source": "isr", "target": "task", "type": "NOTIFIES_RTOS_TASK"},
            enriched["edges"],
        )
        self.assertEqual(
            enriched["embedded"]["rtos"]["notifications"][0]["notifies"],
            "packet_processor_task",
        )


class WebUiBackendTests(unittest.TestCase):
    def test_source_endpoint_returns_selected_function_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.c").write_text(
                "int helper(void) {\n  return 1;\n}\n",
                encoding="utf-8",
            )
            graph_path = root / "enriched_project_graph.json"
            graph_path.write_text(
                json.dumps(
                    {
                        "project": {"source_root": str(root)},
                        "statistics": {},
                        "files": [],
                        "functions": [
                            {
                                "id": "helper",
                                "name": "helper",
                                "classification": "FUNCTION",
                                "location": {
                                    "file": "src/main.c",
                                    "body_start": 1,
                                    "body_end": 3,
                                },
                            }
                        ],
                        "edges": [],
                    }
                ),
                encoding="utf-8",
            )
            original = web_app.DATA_FILE
            web_app.DATA_FILE = graph_path
            try:
                response = app.test_client().get("/api/source/helper")
            finally:
                web_app.DATA_FILE = original

        self.assertEqual(response.status_code, 200)
        self.assertIn("return 1;", response.get_json()["source"])

    def test_ask_ai_endpoint_writes_context_file_with_related_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.c").write_text(
                """void caller(void)
{
  selected();
}

void selected(void)
{
  callee();
}

void callee(void)
{
}
""",
                encoding="utf-8",
            )
            graph_path = root / "enriched_project_graph.json"
            graph_path.write_text(
                json.dumps(
                    {
                        "project": {"source_root": str(root)},
                        "statistics": {},
                        "files": [],
                        "functions": [
                            {"id": "caller", "name": "caller", "classification": "FUNCTION", "location": {"file": "src/main.c", "body_start": 2, "body_end": 4}},
                            {"id": "selected", "name": "selected", "classification": "FUNCTION", "location": {"file": "src/main.c", "body_start": 7, "body_end": 9}},
                            {"id": "callee", "name": "callee", "classification": "FUNCTION", "location": {"file": "src/main.c", "body_start": 12, "body_end": 13}},
                        ],
                        "edges": [
                            {"source": "caller", "target": "selected", "type": "CALLS"},
                            {"source": "selected", "target": "callee", "type": "CALLS"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            original_data = web_app.DATA_FILE
            original_context_dir = web_app.AI_CONTEXT_DIR
            web_app.DATA_FILE = graph_path
            web_app.AI_CONTEXT_DIR = root / "ai_context"
            try:
                response = app.test_client().post(
                    "/api/ask",
                    json={"symbol_id": "selected", "question": "Ne yapiyor?"},
                )
            finally:
                web_app.DATA_FILE = original_data
                web_app.AI_CONTEXT_DIR = original_context_dir

            payload = response.get_json()
            context_path = Path(payload["context_file"])
            text = context_path.read_text(encoding="utf-8")

        self.assertEqual(response.status_code, 501)
        self.assertIn("Ne yapiyor?", text)
        self.assertIn("Called by", text)
        self.assertIn("caller", text)
        self.assertIn("Calls", text)
        self.assertIn("callee", text)

    def test_normalize_graph_uses_top_level_functions_when_files_are_absent(self):
        normalized = normalize_graph(
            {
                "statistics": {"callbacks": 1},
                "functions": [
                    {
                        "id": "cb",
                        "name": "HAL_UART_RxCpltCallback",
                        "classification": "CALLBACK",
                        "file": "Src/main.c",
                        "location": {"line": 12},
                        "documentation": {"brief": "UART receive complete"},
                        "embedded": {
                            "type": "CALLBACK",
                            "callback": {
                                "peripheral": "UART",
                                "event": "RxComplete",
                                "registrations": [
                                    {
                                        "registered_by": "main",
                                        "api": "HAL_UART_RegisterCallback",
                                    }
                                ],
                            },
                        },
                    }
                ],
                "nodes": [],
                "edges": [],
            }
        )

        self.assertEqual(len(normalized["nodes"]), 1)
        self.assertEqual(normalized["nodes"][0]["type"], "CALLBACK")
        self.assertEqual(
            normalized["nodes"][0]["embedded"]["callback"]["event"],
            "RxComplete",
        )

    def test_normalize_graph_prefers_classification_over_generic_kind(self):
        normalized = normalize_graph(
            {
                "statistics": {},
                "files": [
                    {
                        "name": "main.c",
                        "functions": [
                            {
                                "id": "task",
                                "name": "packet_processor_task",
                                "kind": "FUNCTION",
                                "classification": "RTOS_TASK",
                                "location": {"file": "src/main.c"},
                            }
                        ],
                    }
                ],
                "edges": [],
            }
        )

        self.assertEqual(normalized["nodes"][0]["type"], "RTOS_TASK")

    def test_ask_ai_endpoint_returns_not_implemented_placeholder(self):
        client = app.test_client()

        response = client.post(
            "/api/ask",
            json={
                "question": "Bu callback ne yapiyor?",
                "symbol_id": "cb",
            },
        )

        self.assertEqual(response.status_code, 501)
        payload = response.get_json()
        self.assertEqual(payload["status"], "not_implemented")
        self.assertIn("symbol_id", payload["received_context"])


if __name__ == "__main__":
    unittest.main()
