#!/usr/bin/env python3

import json
import re
import sys
from pathlib import Path


DEFAULT_GRAPH = "project_graph.json"
DEFAULT_OUTPUT = "enriched_project_graph.json"


# ============================================================
# Helpers
# ============================================================

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
        )


def read_source(project_root, relative_path):
    if not relative_path:
        return ""

    path = Path(project_root) / relative_path

    if not path.exists():
        return ""

    try:
        return path.read_text(
            encoding="utf-8",
            errors="ignore",
        )
    except OSError:
        return ""


def clean_string(value):
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    return value


# ============================================================
# Source analysis helpers
# ============================================================

def remove_comments(source):
    """
    Remove C/C++ comments while keeping strings reasonably intact.
    This is intentionally lightweight; it is not a C parser.
    """

    def preserve_newlines(match):
        value = match.group(0)
        return "\n" * value.count("\n")

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


def normalize_function_name(name):
    if not name:
        return None

    name = name.strip()

    # Function pointer / whitespace cleanup
    name = re.sub(r"\s+", " ", name)

    return name


def function_name_exists(function_by_name, name):
    return name in function_by_name


def function_source_file(function):
    location = function.get(
        "location",
        {},
    ) or {}

    return (
        location.get("file")
        or function.get("file")
    )


# ============================================================
# RTOS
# ============================================================

RTOS_CREATE_APIS = [
    "xTaskCreate",
    "xTaskCreateStatic",
    "xTaskCreatePinnedToCore",
    "xTaskCreateRestricted",
    "xTaskCreateRestrictedStatic",
]

RTOS_NOTIFY_APIS = [
    "xTaskNotify",
    "xTaskNotifyFromISR",
    "xTaskNotifyGive",
    "vTaskNotifyGiveFromISR",
]


def find_matching_parenthesis(source, opening_index):
    """
    Given the index of '(' find its matching ')'.
    Handles strings and basic comments reasonably.
    """

    depth = 0
    in_string = False
    string_char = None
    escape = False

    for i in range(opening_index, len(source)):

        ch = source[i]

        if in_string:

            if escape:
                escape = False
                continue

            if ch == "\\":
                escape = True
                continue

            if ch == string_char:
                in_string = False

            continue

        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            continue

        if ch == "(":
            depth += 1

        elif ch == ")":
            depth -= 1

            if depth == 0:
                return i

    return None


def split_arguments(argument_string):
    """
    Split C function arguments.

    Handles nested:
      foo(a, bar(b, c), "x,y")
    """

    args = []

    current = []
    depth = 0
    in_string = False
    string_char = None
    escape = False

    for ch in argument_string:

        if in_string:

            current.append(ch)

            if escape:
                escape = False
                continue

            if ch == "\\":
                escape = True
                continue

            if ch == string_char:
                in_string = False

            continue

        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            current.append(ch)
            continue

        if ch in "([{":
            depth += 1
            current.append(ch)
            continue

        if ch in ")]}":
            depth -= 1
            current.append(ch)
            continue

        if ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue

        current.append(ch)

    if current:
        args.append("".join(current).strip())

    return args


def strip_casts(value):
    value = re.sub(
        r"^\s*\([^()]*\)\s*",
        "",
        value,
    )

    return value.strip()


def extract_identifier(value):
    """
    Extract the most likely C identifier from an argument.
    """

    if not value:
        return None

    value = strip_casts(value)

    # &function
    value = re.sub(r"^\s*&\s*", "", value)

    # function(...)
    match = re.search(
        r"\b([A-Za-z_]\w*)\s*$",
        value,
    )

    if match:
        return match.group(1)

    return None


def normalize_handle_argument(value):
    identifier = extract_identifier(value)

    if identifier:
        return identifier

    return clean_string(value)


def analyze_rtos(source, function_by_name):
    """
    Find RTOS task creation calls.

    Example:

        xTaskCreate(
            packet_processor_task,
            "packet",
            512,
            NULL,
            3,
            NULL
        );
    """

    tasks = []

    for api in RTOS_CREATE_APIS:

        pattern = re.compile(
            rf"\b{re.escape(api)}\s*\(",
            re.MULTILINE,
        )

        for match in pattern.finditer(source):

            open_index = source.find(
                "(",
                match.start(),
            )

            close_index = find_matching_parenthesis(
                source,
                open_index,
            )

            if close_index is None:
                continue

            argument_string = source[
                open_index + 1 : close_index
            ]

            args = split_arguments(argument_string)

            if not args:
                continue

            # FreeRTOS task APIs use task function as first argument.
            task_function = extract_identifier(args[0])

            if not task_function:
                continue

            if not function_name_exists(
                function_by_name,
                task_function,
            ):
                # Could still be a macro/function pointer.
                # Do not create a fake function node.
                continue

            task = {
                "function": task_function,
                "api": api,
                "arguments": args,
                "source_offset": match.start(),
                "source_line": source_line_for_offset(
                    source,
                    match.start(),
                ),
            }

            # ------------------------------------------------
            # FreeRTOS xTaskCreate:
            #
            # pxTaskCode
            # pcName
            # usStackDepth
            # pvParameters
            # uxPriority
            # pxCreatedTask
            # ------------------------------------------------

            if len(args) >= 2:
                task["name"] = clean_string(args[1])

            if len(args) >= 3:
                task["stack_size"] = clean_string(args[2])

            if len(args) >= 5:
                task["priority"] = clean_string(args[4])

            if len(args) >= 6:
                task["handle"] = normalize_handle_argument(args[5])

            tasks.append(task)

    return tasks


def analyze_rtos_notifications(functions, source_by_file, rtos_tasks):
    notifications = []
    tasks_by_handle = {
        task.get("handle"): task
        for task in rtos_tasks
        if task.get("handle") and task.get("handle") != "NULL"
    }

    if not tasks_by_handle:
        return notifications

    for file_name, source in source_by_file.items():
        if not source:
            continue

        clean_source = remove_comments(source)

        for api in RTOS_NOTIFY_APIS:
            pattern = re.compile(
                rf"\b{re.escape(api)}\s*\(",
                re.MULTILINE,
            )

            for match in pattern.finditer(clean_source):
                open_index = clean_source.find("(", match.start())
                close_index = find_matching_parenthesis(clean_source, open_index)

                if close_index is None:
                    continue

                args = split_arguments(clean_source[open_index + 1 : close_index])

                if not args:
                    continue

                handle = normalize_handle_argument(args[0])
                task = tasks_by_handle.get(handle)

                if not task:
                    continue

                source_line = source_line_for_offset(clean_source, match.start())
                notifier = find_containing_function_by_line(
                    functions,
                    file_name,
                    source_line,
                )

                notifications.append({
                    "notifier": notifier["name"] if notifier else None,
                    "notifies": task["function"],
                    "api": api,
                    "handle": handle,
                    "source_file": file_name,
                    "source_line": source_line,
                })

    return notifications


# ============================================================
# ISR / Interrupt analysis
# ============================================================

ISR_PATTERNS = [
    re.compile(r"\b([A-Za-z_]\w*_IRQHandler)\s*\("),
    re.compile(r"\b(WWDG_IRQHandler)\s*\("),
    re.compile(r"\b(PVD_IRQHandler)\s*\("),
    re.compile(r"\b(TAMPER_IRQHandler)\s*\("),
    re.compile(r"\b(RTC_IRQHandler)\s*\("),
    re.compile(r"\b(FLASH_IRQHandler)\s*\("),
    re.compile(r"\b(RCC_IRQHandler)\s*\("),
    re.compile(r"\b(EXTI\d+_IRQHandler)\s*\("),
    re.compile(r"\b(DMA\d+_Channel\d+_IRQHandler)\s*\("),
    re.compile(r"\b(ADC\d+_IRQHandler)\s*\("),
    re.compile(r"\b(USB_HP_CAN\d+_TX_IRQHandler)\s*\("),
    re.compile(r"\b(USB_LP_CAN\d+_RX0_IRQHandler)\s*\("),
    re.compile(r"\b(CAN\d+_RX\d+_IRQHandler)\s*\("),
    re.compile(r"\b(TIM\d+_IRQHandler)\s*\("),
    re.compile(r"\b(I2C\d+_EV_IRQHandler)\s*\("),
    re.compile(r"\b(I2C\d+_ER_IRQHandler)\s*\("),
    re.compile(r"\b(SPI\d+_IRQHandler)\s*\("),
    re.compile(r"\b(USART\d+_IRQHandler)\s*\("),
    re.compile(r"\b(UART\d+_IRQHandler)\s*\("),
]


def detect_isr(function_name, source):
    """
    Determine whether a function is an ISR.

    First rely on Doxygen-discovered function name.
    Then verify its presence in source.
    """

    for pattern in ISR_PATTERNS:

        if pattern.search(
            f"{function_name}("
        ):
            return {
                "type": "ISR",
                "name": function_name,
            }

    return None


def extract_peripheral_from_isr(name):

    patterns = [
        (r"^(USART\d+)_IRQHandler$", "USART"),
        (r"^(UART\d+)_IRQHandler$", "UART"),
        (r"^(SPI\d+)_IRQHandler$", "SPI"),
        (r"^(I2C\d+)_EV_IRQHandler$", "I2C"),
        (r"^(I2C\d+)_ER_IRQHandler$", "I2C"),
        (r"^(TIM\d+)_IRQHandler$", "TIMER"),
        (r"^(ADC\d+)_IRQHandler$", "ADC"),
        (r"^(DMA\d+_Channel\d+)_IRQHandler$", "DMA"),
        (r"^(EXTI\d+)_IRQHandler$", "EXTI"),
    ]

    for pattern, peripheral_type in patterns:

        match = re.match(pattern, name)

        if match:
            return {
                "type": peripheral_type,
                "instance": match.group(1),
            }

    return None


def analyze_interrupts(functions, source_by_file):
    interrupts = []

    for function in functions:

        name = function.get("name")

        if not name:
            continue

        detected = detect_isr(
            name,
            source_by_file.get(
                function.get("file"),
                "",
            ),
        )

        if not detected:
            continue

        interrupt = {
            "function": name,
            "type": "ISR",
            "location": function.get("location"),
        }

        peripheral = extract_peripheral_from_isr(name)

        if peripheral:
            interrupt["peripheral"] = peripheral

        interrupts.append(interrupt)

    return interrupts


# ============================================================
# Callback analysis
# ============================================================

CALLBACK_PATTERN = re.compile(
    r"^[A-Za-z_]\w*Callback$"
)


def classify_callback(name):

    if not name:
        return None

    if CALLBACK_PATTERN.match(name):
        return "CALLBACK"

    return None


def callback_category(name):

    categories = {
        "HAL_UART_RxCpltCallback": {
            "peripheral": "UART",
            "event": "RxComplete",
        },

        "HAL_UART_TxCpltCallback": {
            "peripheral": "UART",
            "event": "TxComplete",
        },

        "HAL_UART_ErrorCallback": {
            "peripheral": "UART",
            "event": "Error",
        },

        "HAL_TIM_PeriodElapsedCallback": {
            "peripheral": "TIMER",
            "event": "PeriodElapsed",
        },

        "HAL_ADC_ConvCpltCallback": {
            "peripheral": "ADC",
            "event": "ConversionComplete",
        },

        "HAL_CAN_RxFifo0MsgPendingCallback": {
            "peripheral": "CAN",
            "event": "RxFifo0MessagePending",
        },

        "HAL_SPI_TxCpltCallback": {
            "peripheral": "SPI",
            "event": "TxComplete",
        },

        "HAL_SPI_RxCpltCallback": {
            "peripheral": "SPI",
            "event": "RxComplete",
        },
    }

    return categories.get(name)


def analyze_callbacks(functions):

    callbacks = []

    for function in functions:

        name = function.get("name")

        if not classify_callback(name):
            continue

        callback = {
            "function": name,
            "type": "CALLBACK",
            "location": function.get("location"),
        }

        known_category = callback_category(name)

        if known_category:
            callback.update(known_category)

        callbacks.append(callback)

    return callbacks


CALLBACK_REGISTRATION_APIS = [
    "HAL_UART_RegisterCallback",
    "HAL_SPI_RegisterCallback",
    "HAL_I2C_RegisterCallback",
    "HAL_TIM_RegisterCallback",
    "HAL_ADC_RegisterCallback",
    "HAL_CAN_RegisterCallback",
]


def source_line_for_offset(source, offset):
    return source.count("\n", 0, offset) + 1


def analyze_callback_registrations(
    functions,
    source_by_file,
    function_by_name,
):
    registrations = []

    for file_name, source in source_by_file.items():
        if not source:
            continue

        clean_source = remove_comments(source)

        for api in CALLBACK_REGISTRATION_APIS:
            pattern = re.compile(
                rf"\b{re.escape(api)}\s*\(",
                re.MULTILINE,
            )

            for match in pattern.finditer(clean_source):
                open_index = clean_source.find(
                    "(",
                    match.start(),
                )
                close_index = find_matching_parenthesis(
                    clean_source,
                    open_index,
                )

                if close_index is None:
                    continue

                args = split_arguments(
                    clean_source[open_index + 1 : close_index]
                )

                if len(args) < 3:
                    continue

                callback_function = extract_identifier(args[2])

                if not callback_function:
                    continue

                if callback_function not in function_by_name:
                    continue

                creator = find_containing_function(
                    functions,
                    file_name,
                    clean_source,
                    match.start(),
                )

                registration = {
                    "function": callback_function,
                    "registered_by": creator["name"] if creator else None,
                    "api": api,
                    "handle": clean_string(args[0]),
                    "callback_id": clean_string(args[1]),
                    "source_file": file_name,
                    "source_line": source_line_for_offset(
                        clean_source,
                        match.start(),
                    ),
                }

                registrations.append(registration)

    return registrations


# ============================================================
# Enrichment
# ============================================================

def enrich_functions(
    functions,
    rtos_tasks,
    interrupts,
    callbacks,
):

    task_by_function = {
        task["function"]: task
        for task in rtos_tasks
    }

    interrupt_by_function = {
        item["function"]: item
        for item in interrupts
    }

    callback_by_function = {
        item["function"]: item
        for item in callbacks
    }

    for function in functions:

        name = function.get("name")

        # --------------------------------------------
        # RTOS
        # --------------------------------------------

        if name in task_by_function:

            task = task_by_function[name]

            function["classification"] = "RTOS_TASK"

            function["embedded"] = {
                "type": "RTOS_TASK",
                "rtos": {
                    key: value
                    for key, value in task.items()
                    if key != "function"
                    and key != "source_offset"
                },
            }

        # --------------------------------------------
        # ISR
        # --------------------------------------------

        elif name in interrupt_by_function:

            interrupt = interrupt_by_function[name]

            function["classification"] = "ISR"

            function["embedded"] = {
                "type": "ISR",
                "interrupt": {
                    key: value
                    for key, value in interrupt.items()
                    if key != "function"
                },
            }

        # --------------------------------------------
        # Callback
        # --------------------------------------------

        elif name in callback_by_function:

            callback = callback_by_function[name]

            function["classification"] = "CALLBACK"

            function["embedded"] = {
                "type": "CALLBACK",
                "callback": {
                    key: value
                    for key, value in callback.items()
                    if key != "function"
                },
            }


def sync_node_classifications(graph):
    function_by_id = {
        function.get("id"): function
        for function in graph.get("functions", [])
        if function.get("id")
    }

    for node in graph.get("nodes", []):
        function = function_by_id.get(node.get("id"))

        if not function:
            continue

        node["type"] = function.get(
            "classification",
            node.get("type"),
        )
        node["data"] = function


# ============================================================
# Graph edges
# ============================================================

def add_embedded_edges(graph, rtos_tasks):

    existing = {
        (
            edge.get("source"),
            edge.get("target"),
            edge.get("type"),
        )
        for edge in graph.get("edges", [])
    }

    function_by_name = {
        function["name"]: function
        for function in graph.get("functions", [])
    }

    for task in rtos_tasks:

        task_function = task["function"]
        task_node = function_by_name.get(task_function)

        if not task_node:
            continue

        created_by = task.get("created_by")

        if not created_by:
            continue

        creator = function_by_name.get(created_by)

        if not creator:
            continue

        edge = (
            creator["id"],
            task_node["id"],
            "CREATES_RTOS_TASK",
        )

        if edge in existing:
            continue

        graph["edges"].append({
            "source": creator["id"],
            "target": task_node["id"],
            "type": "CREATES_RTOS_TASK",
        })

        existing.add(edge)


def add_callback_edges(graph, callback_registrations):
    existing = {
        (
            edge.get("source"),
            edge.get("target"),
            edge.get("type"),
        )
        for edge in graph.get("edges", [])
    }

    function_by_name = {
        function["name"]: function
        for function in graph.get("functions", [])
    }

    for registration in callback_registrations:
        callback = function_by_name.get(
            registration.get("function")
        )
        registrar = function_by_name.get(
            registration.get("registered_by")
        )

        if not callback or not registrar:
            continue

        edge = (
            registrar["id"],
            callback["id"],
            "REGISTERS_CALLBACK",
        )

        if edge in existing:
            continue

        graph["edges"].append({
            "source": registrar["id"],
            "target": callback["id"],
            "type": "REGISTERS_CALLBACK",
        })

        existing.add(edge)


def add_rtos_notification_edges(graph, notifications):
    existing = {
        (
            edge.get("source"),
            edge.get("target"),
            edge.get("type"),
        )
        for edge in graph.get("edges", [])
    }

    function_by_name = {
        function["name"]: function
        for function in graph.get("functions", [])
    }

    for notification in notifications:
        notifier = function_by_name.get(
            notification.get("notifier")
        )
        task = function_by_name.get(
            notification.get("notifies")
        )

        if not notifier or not task:
            continue

        edge = (
            notifier["id"],
            task["id"],
            "NOTIFIES_RTOS_TASK",
        )

        if edge in existing:
            continue

        graph["edges"].append({
            "source": notifier["id"],
            "target": task["id"],
            "type": "NOTIFIES_RTOS_TASK",
        })
        existing.add(edge)


# ============================================================
# Main analysis
# ============================================================

def analyze_project(
    graph_path,
    output_path,
    project_root,
):

    print("======================================")
    print(" Embedded Project Analyzer")
    print("======================================")
    print()

    graph = load_json(graph_path)

    functions = graph.get(
        "functions",
        [],
    )

    print(
        f"[INFO] Functions in graph: {len(functions)}"
    )

    # --------------------------------------------------------
    # Function lookup
    # --------------------------------------------------------

    function_by_name = {
        function["name"]: function
        for function in functions
        if function.get("name")
    }

    # --------------------------------------------------------
    # Read source files
    # --------------------------------------------------------

    source_by_file = {}

    files = set()

    for function in functions:

        location = function.get(
            "location",
            {},
        )

        file_name = location.get("file")

        if file_name:
            files.add(file_name)

    print(
        f"[INFO] Source files referenced: {len(files)}"
    )

    for file_name in files:

        source = read_source(
            project_root,
            file_name,
        )

        source_by_file[file_name] = source

    # --------------------------------------------------------
    # RTOS
    # --------------------------------------------------------

    all_rtos_tasks = []

    for file_name, source in source_by_file.items():

        if not source:
            continue

        source = remove_comments(source)

        tasks = analyze_rtos(
            source,
            function_by_name,
        )

        for task in tasks:

            # Determine creator function from graph locations.
            offset = task.get(
                "source_offset",
                0,
            )

            creator = None

            # Find function whose source location surrounds
            # the xTaskCreate call.
            for function in functions:

                if function_source_file(function) != file_name:
                    continue

                location = function.get(
                    "location",
                    {},
                )

                start = location.get(
                    "body_start"
                )

                end = location.get(
                    "body_end"
                )

                if start is None:
                    continue

                # We don't have exact offset here, so this
                # remains optional. Later this can be improved
                # with line-based source mapping.
                creator = None

            task["source_file"] = file_name

            all_rtos_tasks.append(task)

    # --------------------------------------------------------
    # Better task creator detection
    # --------------------------------------------------------

    for task in all_rtos_tasks:

        source_file = task.get(
            "source_file"
        )

        source = source_by_file.get(
            source_file,
            "",
        )

        offset = task.get(
            "source_offset",
            -1,
        )
        source_line = task.get(
            "source_line",
        )

        if source_line is None and offset < 0:
            continue

        if source_line is not None:
            creator = find_containing_function_by_line(
                functions,
                source_file,
                source_line,
            )
        else:
            creator = find_containing_function(
                functions,
                source_file,
                source,
                offset,
            )

        if creator:
            task["created_by"] = creator["name"]

    rtos_notifications = analyze_rtos_notifications(
        functions,
        source_by_file,
        all_rtos_tasks,
    )

    # --------------------------------------------------------
    # Interrupts
    # --------------------------------------------------------

    interrupts = analyze_interrupts(
        functions,
        source_by_file,
    )

    # --------------------------------------------------------
    # Callbacks
    # --------------------------------------------------------

    callbacks = analyze_callbacks(
        functions,
    )

    callback_registrations = analyze_callback_registrations(
        functions,
        source_by_file,
        function_by_name,
    )

    registrations_by_function = {}

    for registration in callback_registrations:
        registrations_by_function.setdefault(
            registration["function"],
            [],
        ).append({
            key: value
            for key, value in registration.items()
            if key != "function" and value is not None
        })

    for callback in callbacks:
        callback["registrations"] = registrations_by_function.get(
            callback["function"],
            [],
        )

    # --------------------------------------------------------
    # Enrich functions
    # --------------------------------------------------------

    enrich_functions(
        functions,
        all_rtos_tasks,
        interrupts,
        callbacks,
    )

    sync_node_classifications(graph)

    # --------------------------------------------------------
    # Add embedded nodes/edges
    # --------------------------------------------------------

    add_embedded_edges(
        graph,
        all_rtos_tasks,
    )

    add_callback_edges(
        graph,
        callback_registrations,
    )

    add_rtos_notification_edges(
        graph,
        rtos_notifications,
    )

    # --------------------------------------------------------
    # Embedded section
    # --------------------------------------------------------

    graph["embedded"] = {
        "rtos": {
            "tasks": all_rtos_tasks,
            "notifications": rtos_notifications,
        },

        "interrupts": interrupts,

        "callbacks": callbacks,
    }

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    graph.setdefault(
        "statistics",
        {},
    )

    graph["statistics"]["rtos_tasks"] = len(
        all_rtos_tasks
    )

    graph["statistics"]["interrupts"] = len(
        interrupts
    )

    graph["statistics"]["callbacks"] = len(
        callbacks
    )

    graph["statistics"]["callback_registrations"] = len(
        callback_registrations
    )

    graph["statistics"]["rtos_notifications"] = len(
        rtos_notifications
    )

    save_json(
        output_path,
        graph,
    )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    print()
    print("======================================")
    print(" Analysis Complete")
    print("======================================")
    print()

    print(
        f"RTOS Tasks : {len(all_rtos_tasks)}"
    )

    print(
        f"Interrupts : {len(interrupts)}"
    )

    print(
        f"Callbacks  : {len(callbacks)}"
    )

    print(
        f"Callback registrations: {len(callback_registrations)}"
    )

    print()
    print("RTOS TASKS")

    for task in all_rtos_tasks:

        print(
            f"  {task['function']}"
            f"  <- {task.get('created_by', '?')}"
            f"  [{task['api']}]"
        )

    print()
    print("INTERRUPTS")

    for interrupt in interrupts:

        peripheral = interrupt.get(
            "peripheral"
        )

        if peripheral:
            peripheral_text = (
                f"{peripheral['type']}"
                f"/{peripheral['instance']}"
            )
        else:
            peripheral_text = "-"

        print(
            f"  {interrupt['function']}"
            f"  [{peripheral_text}]"
        )

    print()
    print("CALLBACKS")

    for callback in callbacks:

        print(
            f"  {callback['function']}"
        )

    print()
    print(
        f"[INFO] Output: {output_path}"
    )


# ============================================================
# Find containing function
# ============================================================

def find_containing_function(
    functions,
    file_name,
    source,
    offset,
):
    """
    Find which function contains a source offset.

    Uses Doxygen body line information plus source line mapping.
    """

    if not source:
        return None

    line = source.count(
        "\n",
        0,
        offset,
    ) + 1

    return find_containing_function_by_line(
        functions,
        file_name,
        line,
    )


def find_containing_function_by_line(
    functions,
    file_name,
    line,
):
    """
    Find which function contains a source line.
    """

    candidates = []

    for function in functions:

        if function_source_file(function) != file_name:
            continue

        location = function.get(
            "location",
            {},
        )

        start = location.get(
            "body_start"
        )

        end = location.get(
            "body_end"
        )

        if start is None:
            continue

        if end is None or end < start:
            # For Doxygen cases where bodyend is unavailable,
            # use a conservative range.
            end = start + 10000

        if start <= line <= end:

            candidates.append(
                (
                    end - start,
                    function,
                )
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0]
    )

    return candidates[0][1]


# ============================================================
# CLI
# ============================================================

def main():

    graph_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else DEFAULT_GRAPH
    )

    output_path = (
        sys.argv[2]
        if len(sys.argv) > 2
        else DEFAULT_OUTPUT
    )

    project_root = (
        sys.argv[3]
        if len(sys.argv) > 3
        else "."
    )

    analyze_project(
        graph_path,
        output_path,
        project_root,
    )


if __name__ == "__main__":
    main()
