#!/usr/bin/env python3

import json
import re
import sys
import argparse
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

DEFAULT_XML_DIR = "doxygen_output/xml"
DEFAULT_OUTPUT = "project_graph.json"
DEFAULT_ENRICHED_OUTPUT = "enriched_project_graph.json"
DEFAULT_DOXYGEN_OUTPUT = "doxygen_output"


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def text(element):
    if element is None:
        return ""

    return "".join(element.itertext()).strip()


def clean_text(value):
    if not value:
        return ""

    value = re.sub(r"\s+", " ", value)
    return value.strip()


def get_child_text(element, tag):
    child = element.find(tag)
    return clean_text(text(child))


def get_location(member):
    location = member.find("location")

    if location is None:
        return {
            "file": None,
            "line": None,
            "column": None,
            "body_start": None,
            "body_end": None,
        }

    return {
        "file": location.get("file"),
        "line": int(location.get("line"))
        if location.get("line", "").isdigit()
        else None,
        "column": int(location.get("column"))
        if location.get("column", "").isdigit()
        else None,
        "body_start": int(location.get("bodystart"))
        if location.get("bodystart", "").isdigit()
        else None,
        "body_end": int(location.get("bodyend"))
        if location.get("bodyend", "").isdigit()
        else None,
    }


def get_description(member):
    brief = member.find("briefdescription")
    detail = member.find("detaileddescription")

    return {
        "brief": clean_text(text(brief)),
        "detail": clean_text(text(detail)),
    }


def get_references(member, tag):
    result = []

    for ref in member.findall(tag):
        refid = ref.get("refid")
        name = clean_text(text(ref))

        result.append({
            "refid": refid,
            "name": name,
        })

    return result


def normalize_repo_path(value):
    if not value:
        return ""

    return str(value).replace("\\", "/").strip("/")


def path_matches_folder(path, folder):
    path = normalize_repo_path(path).lower()
    folder = normalize_repo_path(folder).lower()

    if not folder:
        return False

    return path == folder or path.startswith(folder + "/")


def is_selected_path(path, include_folders=None, exclude_folders=None):
    include_folders = include_folders or []
    exclude_folders = exclude_folders or []

    if include_folders and not any(
        path_matches_folder(path, folder)
        for folder in include_folders
    ):
        return False

    if any(
        path_matches_folder(path, folder)
        for folder in exclude_folders
    ):
        return False

    return True


def parsed_candidate_paths(parsed):
    paths = [parsed.get("name")]

    for function in parsed.get("functions", []):
        paths.append(
            function.get("location", {}).get("file")
            or function.get("file")
        )

    for define in parsed.get("defines", []):
        paths.append(
            define.get("location", {}).get("file")
            or define.get("file")
        )

    return [
        path
        for path in paths
        if path
    ]


def parsed_matches_filters(parsed, include_folders=None, exclude_folders=None):
    paths = parsed_candidate_paths(parsed)

    if include_folders:
        include_match = any(
            any(
                path_matches_folder(path, folder)
                for folder in include_folders
            )
            for path in paths
        )

        if not include_match:
            return False

    if exclude_folders:
        exclude_match = any(
            any(
                path_matches_folder(path, folder)
                for folder in exclude_folders
            )
            for path in paths
        )

        if exclude_match:
            return False

    return True


def remove_c_comments_keep_lines(source):
    def preserve_newlines(match):
        return "\n" * match.group(0).count("\n")

    source = re.sub(
        r"/\*.*?\*/",
        preserve_newlines,
        source,
        flags=re.DOTALL,
    )
    source = re.sub(
        r"//.*?$",
        "",
        source,
        flags=re.MULTILINE,
    )
    return source


def strip_c_strings(source):
    return re.sub(
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        '""',
        source,
    )


def function_location_file(function):
    location = function.get("location") or {}
    return location.get("file") or function.get("file")


def read_source_file(project_root, relative_path):
    if not project_root or not relative_path:
        return ""

    path = Path(project_root) / relative_path

    if not path.exists():
        return ""

    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def source_body_for_function(source, function):
    location = function.get("location") or {}
    start = location.get("body_start")
    end = location.get("body_end")

    if not source or start is None or end is None or end < start:
        return ""

    lines = source.splitlines()
    return "\n".join(lines[start - 1 : end])


def add_source_call_edges(edges, functions, project_root):
    if not project_root:
        return

    existing = {
        (
            edge.get("source"),
            edge.get("target"),
            edge.get("type"),
        )
        for edge in edges
    }

    functions_by_name = {}

    for function in functions:
        functions_by_name.setdefault(
            function["name"],
            [],
        ).append(function)

    source_cache = {}

    for function in functions:
        source_file = function_location_file(function)

        if not source_file:
            continue

        if source_file not in source_cache:
            source_cache[source_file] = strip_c_strings(
                remove_c_comments_keep_lines(
                    read_source_file(project_root, source_file)
                )
            )

        body = source_body_for_function(
            source_cache[source_file],
            function,
        )

        if not body:
            continue

        for callee_name, candidates in functions_by_name.items():
            if callee_name == function["name"]:
                continue

            if not re.search(
                rf"\b{re.escape(callee_name)}\s*\(",
                body,
            ):
                continue

            same_file_candidates = [
                candidate
                for candidate in candidates
                if function_location_file(candidate) == source_file
            ]
            callee = (
                same_file_candidates[0]
                if same_file_candidates
                else candidates[0]
            )

            edge = (
                function["id"],
                callee["id"],
                "CALLS",
            )

            if edge in existing:
                continue

            edges.append({
                "source": function["id"],
                "target": callee["id"],
                "type": "CALLS",
            })
            existing.add(edge)


def is_function_definition(function):
    location = function.get("location") or {}
    return (
        location.get("body_start") is not None
        and location.get("body_end") is not None
    )


def function_dedupe_key(function):
    return (
        function.get("name"),
        function.get("args") or "",
    )


def function_definition_score(function):
    source_file = function_location_file(function) or ""
    suffix_score = 1 if source_file.lower().endswith((".c", ".cpp", ".cc", ".cxx")) else 0
    body_score = 2 if is_function_definition(function) else 0
    return body_score + suffix_score


def dedupe_function_declarations(files):
    chosen_by_key = {}

    for file_info in files:
        for function in file_info.get("functions", []):
            key = function_dedupe_key(function)
            current = chosen_by_key.get(key)

            if current is None or function_definition_score(function) > function_definition_score(current):
                chosen_by_key[key] = function

    chosen_ids = {
        function.get("id")
        for function in chosen_by_key.values()
        if function.get("id")
    }

    for file_info in files:
        file_info["functions"] = [
            function
            for function in file_info.get("functions", [])
            if function.get("id") in chosen_ids
        ]

    return [
        function
        for file_info in files
        for function in file_info.get("functions", [])
    ]


def quote_doxy_path(path):
    value = normalize_repo_path(path)

    if not value:
        return ""

    if re.search(r"\s", value):
        return f'"{value}"'

    return value


def format_doxy_list(values):
    return " ".join(
        quote_doxy_path(value)
        for value in values
        if normalize_repo_path(value)
    )


def format_doxy_assignment(tag, value):
    return f"{tag:<23}= {value}"


def override_doxyfile_tags(content, overrides):
    lines = content.splitlines()
    seen = set()
    result = []

    for line in lines:
        replaced = False

        for tag, value in overrides.items():
            if re.match(rf"^\s*{re.escape(tag)}\s*=", line):
                result.append(
                    format_doxy_assignment(tag, value)
                )
                seen.add(tag)
                replaced = True
                break

        if not replaced:
            result.append(line)

    missing = [
        tag
        for tag in overrides
        if tag not in seen
    ]

    if missing:
        result.append("")
        result.append("# Embedded Project Explorer overrides")

        for tag in missing:
            result.append(
                format_doxy_assignment(tag, overrides[tag])
            )

    return "\n".join(result) + "\n"


def write_generated_doxyfile(
    path,
    template_path,
    include_folders=None,
    exclude_folders=None,
    output_directory=DEFAULT_DOXYGEN_OUTPUT,
):
    include_folders = include_folders or ["."]
    exclude_folders = exclude_folders or []
    template_path = Path(template_path)

    if not template_path.exists():
        raise FileNotFoundError(
            f"Doxyfile template not found: {template_path}"
        )

    template = template_path.read_text(
        encoding="utf-8",
        errors="ignore",
    )

    overrides = {
        "OUTPUT_DIRECTORY": quote_doxy_path(output_directory),
        "INPUT": format_doxy_list(include_folders),
        "EXCLUDE": format_doxy_list(exclude_folders),
        "RECURSIVE": "YES",
        "EXTRACT_ALL": "YES",
        "EXTRACT_STATIC": "YES",
        "FULL_PATH_NAMES": "YES",
        "STRIP_FROM_PATH": ".",
        "SOURCE_BROWSER": "YES",
        "INLINE_SOURCES": "YES",
        "GENERATE_HTML": "NO",
        "GENERATE_XML": "YES",
        "REFERENCES_RELATION": "YES",
        "REFERENCED_BY_RELATION": "YES",
    }

    path.write_text(
        override_doxyfile_tags(template, overrides),
        encoding="utf-8",
    )


def default_run_command(command, cwd):
    subprocess.run(
        command,
        cwd=cwd,
        check=True,
    )


def copy_doxygen_output(
    source_output_dir,
    workspace_root,
    local_output_dir=DEFAULT_DOXYGEN_OUTPUT,
):
    source_output_dir = Path(source_output_dir)
    workspace_root = Path(workspace_root)
    destination = workspace_root / local_output_dir

    if not source_output_dir.exists():
        raise FileNotFoundError(
            f"Doxygen output directory not found: {source_output_dir}"
        )

    if destination.exists():
        shutil.rmtree(destination)

    shutil.copytree(source_output_dir, destination)

    xml_dir = destination / "xml"

    if not xml_dir.exists():
        raise FileNotFoundError(
            f"Doxygen XML directory not found after copy: {xml_dir}"
        )

    return xml_dir


def run_doxygen_pipeline(
    target_project,
    workspace_root=".",
    include_folders=None,
    exclude_folders=None,
    doxygen_bin="doxygen",
    doxygen_output=DEFAULT_DOXYGEN_OUTPUT,
    local_doxygen_output=DEFAULT_DOXYGEN_OUTPUT,
    generated_doxyfile=".embedded_explorer.Doxyfile",
    template_doxyfile=None,
    run_command=default_run_command,
):
    target_project = Path(target_project).resolve()
    workspace_root = Path(workspace_root).resolve()

    if not target_project.exists():
        raise FileNotFoundError(f"Target project not found: {target_project}")

    doxyfile_path = target_project / generated_doxyfile
    template_doxyfile = (
        Path(template_doxyfile)
        if template_doxyfile
        else workspace_root / "Doxyfile"
    )

    write_generated_doxyfile(
        doxyfile_path,
        template_path=template_doxyfile,
        include_folders=include_folders,
        exclude_folders=exclude_folders,
        output_directory=doxygen_output,
    )

    print(f"[INFO] Running Doxygen in target project: {target_project}")
    print(f"[INFO] Generated Doxyfile: {doxyfile_path}")

    run_command(
        [doxygen_bin, str(doxyfile_path.name)],
        cwd=target_project,
    )

    xml_dir = copy_doxygen_output(
        target_project / doxygen_output,
        workspace_root,
        local_output_dir=local_doxygen_output,
    )

    print(f"[INFO] Copied Doxygen output to: {workspace_root / local_doxygen_output}")

    return xml_dir


# ------------------------------------------------------------
# Embedded classification
# ------------------------------------------------------------

ISR_PATTERNS = [
    r".*_IRQHandler$",
    r"^HardFault_Handler$",
    r"^MemManage_Handler$",
    r"^BusFault_Handler$",
    r"^UsageFault_Handler$",
    r"^SVC_Handler$",
    r"^DebugMon_Handler$",
    r"^PendSV_Handler$",
    r"^SysTick_Handler$",
]

CALLBACK_PATTERNS = [
    r"^HAL_.*Callback$",
    r".*Callback$",
    r".*_callback$",
    r".*_cb$",
]

TASK_NAME_PATTERNS = [
    r".*[Tt]ask$",
    r".*[Tt]ask[A-Z_].*",
]


def classify_function(name):
    for pattern in ISR_PATTERNS:
        if re.match(pattern, name):
            return "ISR"

    for pattern in CALLBACK_PATTERNS:
        if re.match(pattern, name):
            return "CALLBACK"

    for pattern in TASK_NAME_PATTERNS:
        if re.match(pattern, name):
            return "RTOS_TASK"

    if name == "main":
        return "ENTRY_POINT"

    return "FUNCTION"


# ------------------------------------------------------------
# Parse function
# ------------------------------------------------------------

def parse_function(member, file_name):
    name = get_child_text(member, "name")

    if not name:
        return None

    function = {
        "id": member.get("id"),
        "name": name,
        "kind": "FUNCTION",
        "type": get_child_text(member, "type"),
        "definition": get_child_text(member, "definition"),
        "args": get_child_text(member, "argsstring"),
        "static": member.get("static") == "yes",
        "documentation": get_description(member),
        "location": get_location(member),
        "file": file_name,
        "references": get_references(member, "references"),
        "referenced_by": get_references(member, "referencedby"),
    }

    function["classification"] = classify_function(name)

    return function


# ------------------------------------------------------------
# Parse XML file
# ------------------------------------------------------------

def parse_xml_file(xml_file):
    try:
        root = ET.parse(xml_file).getroot()
    except ET.ParseError as exc:
        print(f"[WARN] XML parse failed: {xml_file}: {exc}")
        return None

    compound = root.find("compounddef")

    if compound is None:
        return None

    compound_name = get_child_text(compound, "compoundname")

    result = {
        "id": compound.get("id"),
        "name": compound_name,
        "kind": compound.get("kind"),
        "language": compound.get("language"),
        "includes": [],
        "functions": [],
        "defines": [],
    }

    # --------------------------------------------------------
    # Includes
    # --------------------------------------------------------

    for include in compound.findall("includes"):
        result["includes"].append({
            "name": clean_text(text(include)),
            "local": include.get("local") == "yes",
        })

    # --------------------------------------------------------
    # Members
    # --------------------------------------------------------

    for section in compound.findall("sectiondef"):
        section_kind = section.get("kind")

        for member in section.findall("memberdef"):
            member_kind = member.get("kind")

            if member_kind == "function":
                function = parse_function(member, compound_name)

                if function:
                    result["functions"].append(function)

            elif member_kind == "define":
                define = {
                    "id": member.get("id"),
                    "name": get_child_text(member, "name"),
                    "initializer": get_child_text(member, "initializer"),
                    "location": get_location(member),
                    "documentation": get_description(member),
                    "file": compound_name,
                }

                result["defines"].append(define)

    return result


# ------------------------------------------------------------
# Build graph
# ------------------------------------------------------------

def build_graph(
    xml_dir,
    output_path=None,
    project_root=".",
    include_folders=None,
    exclude_folders=None,
):
    xml_dir = Path(xml_dir)

    files = []
    functions = []
    defines = []

    print(f"[INFO] XML directory: {xml_dir}")

    if include_folders:
        print(f"[INFO] Include folders: {', '.join(include_folders)}")

    if exclude_folders:
        print(f"[INFO] Exclude folders: {', '.join(exclude_folders)}")

    xml_files = sorted(xml_dir.glob("*.xml"))

    print(f"[INFO] XML files found: {len(xml_files)}")

    for xml_file in xml_files:

        # index.xml etc.
        if xml_file.name in {
            "index.xml",
        }:
            continue

        parsed = parse_xml_file(xml_file)

        if not parsed:
            continue

        if parsed.get("kind") != "file":
            continue

        if not parsed_matches_filters(
            parsed,
            include_folders=include_folders,
            exclude_folders=exclude_folders,
        ):
            continue

        parsed["functions"] = [
            function
            for function in parsed["functions"]
            if is_selected_path(
                function.get("location", {}).get("file")
                or function.get("file"),
                include_folders=include_folders,
                exclude_folders=exclude_folders,
            )
        ]

        parsed["defines"] = [
            define
            for define in parsed["defines"]
            if is_selected_path(
                define.get("location", {}).get("file")
                or define.get("file"),
                include_folders=include_folders,
                exclude_folders=exclude_folders,
            )
        ]

        files.append(parsed)

        functions.extend(parsed["functions"])
        defines.extend(parsed["defines"])

    functions = dedupe_function_declarations(files)
    defines = [
        define
        for file_info in files
        for define in file_info.get("defines", [])
    ]

    # --------------------------------------------------------
    # Build lookup tables
    # --------------------------------------------------------

    function_by_id = {
        f["id"]: f
        for f in functions
        if f.get("id")
    }

    function_by_name = {}

    for function in functions:
        function_by_name.setdefault(
            function["name"],
            []
        ).append(function)

    # --------------------------------------------------------
    # Create graph nodes
    # --------------------------------------------------------

    nodes = []

    for file_info in files:

        nodes.append({
            "id": file_info["id"],
            "type": "FILE",
            "name": file_info["name"],
            "data": {
                "kind": file_info["kind"],
                "includes": file_info["includes"],
            },
        })

    for function in functions:

        nodes.append({
            "id": function["id"],
            "type": function["classification"],
            "name": function["name"],
            "data": function,
        })

    # --------------------------------------------------------
    # Create edges
    # --------------------------------------------------------

    edges = []

    # Function calls
    for function in functions:

        source_id = function["id"]

        for reference in function["references"]:

            target_id = reference.get("refid")

            if target_id and target_id in function_by_id:

                edges.append({
                    "source": source_id,
                    "target": target_id,
                    "type": "CALLS",
                })

    # Function callers
    for function in functions:

        target_id = function["id"]

        for reference in function["referenced_by"]:

            source_id = reference.get("refid")

            if source_id and source_id in function_by_id:

                edge = {
                    "source": source_id,
                    "target": target_id,
                    "type": "CALLS",
                }

                # Don't duplicate
                if edge not in edges:
                    edges.append(edge)

    add_source_call_edges(
        edges,
        functions,
        project_root,
    )

    # --------------------------------------------------------
    # Final graph
    # --------------------------------------------------------

    graph = {
        "schema_version": "1.0",

        "project": {
            "name": "Embedded Project",
            "source_root": str(project_root),
        },

        "statistics": {
            "files": len(files),
            "functions": len(functions),
            "defines": len(defines),
            "nodes": len(nodes),
            "edges": len(edges),
        },

        "files": files,

        "functions": functions,

        "defines": defines,

        "nodes": nodes,

        "edges": edges,
    }

    return graph


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Convert Doxygen XML into an embedded project graph.",
    )

    parser.add_argument(
        "xml_dir",
        nargs="?",
        default=DEFAULT_XML_DIR,
        help="Doxygen XML directory.",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=DEFAULT_OUTPUT,
        help="Output project graph JSON path.",
    )
    parser.add_argument(
        "--enriched-output",
        default=DEFAULT_ENRICHED_OUTPUT,
        help="Output enriched project graph JSON path.",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Source repository root used by later analyzer stages.",
    )
    parser.add_argument(
        "--target-project",
        help="Run Doxygen inside this source project, then copy generated output into this workspace.",
    )
    parser.add_argument(
        "--doxygen-bin",
        default="doxygen",
        help="Doxygen executable name or path.",
    )
    parser.add_argument(
        "--doxygen-output",
        default=DEFAULT_DOXYGEN_OUTPUT,
        help="Doxygen output directory inside the target project.",
    )
    parser.add_argument(
        "--local-doxygen-output",
        default=DEFAULT_DOXYGEN_OUTPUT,
        help="Directory in this workspace where generated Doxygen output will be copied.",
    )
    parser.add_argument(
        "--generated-doxyfile",
        default=".embedded_explorer.Doxyfile",
        help="Temporary Doxyfile name written inside the target project.",
    )
    parser.add_argument(
        "--include-folder",
        action="append",
        default=[],
        help="Only include files below this folder. Can be repeated.",
    )
    parser.add_argument(
        "--exclude-folder",
        action="append",
        default=[],
        help="Exclude files below this folder. Can be repeated.",
    )

    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    xml_dir = args.xml_dir
    project_root = args.project_root

    if args.target_project:
        xml_dir = run_doxygen_pipeline(
            target_project=args.target_project,
            workspace_root=Path.cwd(),
            include_folders=args.include_folder,
            exclude_folders=args.exclude_folder,
            doxygen_bin=args.doxygen_bin,
            doxygen_output=args.doxygen_output,
            local_doxygen_output=args.local_doxygen_output,
            generated_doxyfile=args.generated_doxyfile,
        )
        project_root = args.target_project

    graph = build_graph(
        xml_dir,
        output_path=args.output,
        project_root=project_root,
        include_folders=args.include_folder,
        exclude_folders=args.exclude_folder,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(
            graph,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print("======================================")
    print(" Project Graph Generated")
    print("======================================")
    print(f"Files     : {graph['statistics']['files']}")
    print(f"Functions : {graph['statistics']['functions']}")
    print(f"Defines   : {graph['statistics']['defines']}")
    print(f"Nodes     : {graph['statistics']['nodes']}")
    print(f"Edges     : {graph['statistics']['edges']}")
    print()
    print(f"Output    : {args.output}")
    print()

    from embedded_analyzer import analyze_project

    analyze_project(
        args.output,
        args.enriched_output,
        project_root,
    )

    print()
    print(f"Enriched output: {args.enriched_output}")
    print()


if __name__ == "__main__":
    main()
