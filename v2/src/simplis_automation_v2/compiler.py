"""Pure v2 circuit compilation: YAML graph to reviewed SIMetrix script and manifest."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .catalog import find_property, get_device, is_fully_ideal, load_catalog, load_embedded_symbol, parse_symbol_libraries
from .errors import CatalogError, ValidationError
from .io import sha256_file, write_json
from .schema import BUILD_MANIFEST_SCHEMA_VERSION, load_circuit, resolve_parameters, validate_circuit
from .units import evaluate_expression, format_simplis


GROUND_NETS = {"0", "GND", "gnd"}


def _quote(value: Any) -> str:
    text = str(value).replace('"', '\\"')
    return f'"{text}"' if not text or any(character.isspace() for character in text) else text


def _resolve_catalog_path(circuit: Mapping[str, Any], circuit_path: Path, explicit: str | Path | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    lock = circuit["catalog_lock"]
    path = Path(str(lock["path"]))
    return (circuit_path.parent / path).resolve() if not path.is_absolute() else path


def _runtime_symbol_dir(catalog: Mapping[str, Any]) -> Path:
    runtime = catalog.get("runtime", {})
    configured = runtime.get("symbol_library_dir") or os.environ.get("SIMPLIS_SYMBOL_LIB_DIR")
    if not configured:
        raise CatalogError("Catalog does not identify a symbol_library_dir")
    return Path(str(configured)).resolve()


def _normalize_orientation(value: Any) -> str:
    text = str(value or "N0").upper()
    if text in {"0", "90", "180", "270"}:
        text = "N" + text
    if text not in {"N0", "N90", "N180", "N270", "M0", "M90", "M180", "M270"}:
        raise ValidationError("Unsupported orientation", orientation=value)
    return text


def _transform_pin(x: int, y: int, orientation: str) -> tuple[int, int]:
    normalized = _normalize_orientation(orientation)
    mirrored = normalized.startswith("M")
    angle = int(normalized[1:])
    if mirrored:
        x = -x
    if angle == 0:
        return x, y
    if angle == 90:
        return -y, x
    if angle == 180:
        return -x, -y
    return y, -x


def _script_orientation(orientation: str) -> str:
    normalized = _normalize_orientation(orientation)
    return {
        "N0": "0",
        "N90": "1",
        "N180": "2",
        "N270": "3",
        "M0": "4",
        "M90": "5",
        "M180": "6",
        "M270": "7",
    }[normalized]


def _term_orientation(dx: int, dy: int) -> str:
    """Point a terminal body away from the component pin it labels."""

    if abs(dx) >= abs(dy):
        return "N180" if dx <= 0 else "N0"
    return "N270" if dy <= 0 else "N90"


_TEXT_CHARACTER_WIDTH = 60
_TEXT_LINE_HEIGHT = 120
_AUTOPOS_GAP = 60
_AUTOPOS_STACK_GAP = 24
_TERMINAL_BODY_LENGTH = 120
_GROUND_BODY_HEIGHT = 360


def _transformed_bbox(raw: Mapping[str, Any], orientation: str) -> dict[str, int]:
    corners = (
        (int(raw["min_x"]), int(raw["min_y"])),
        (int(raw["min_x"]), int(raw["max_y"])),
        (int(raw["max_x"]), int(raw["min_y"])),
        (int(raw["max_x"]), int(raw["max_y"])),
    )
    transformed = [_transform_pin(x, y, orientation) for x, y in corners]
    xs = [item[0] for item in transformed]
    ys = [item[1] for item in transformed]
    return {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)}


def _symbol_body_bbox(component: Mapping[str, Any], orientation: str) -> dict[str, int]:
    raw = component["symbol_record"].get("drawing_bbox") or component["symbol_record"].get("bbox", {})
    if not isinstance(raw, Mapping) or not all(key in raw for key in ("min_x", "min_y", "max_x", "max_y")):
        pins = component["symbol_record"].get("pins", [])
        xs = [int(pin["x"]) for pin in pins] or [0]
        ys = [int(pin["y"]) for pin in pins] or [0]
        raw = {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)}
    return _transformed_bbox(raw, orientation)


def _pin_term_orientation(component: Mapping[str, Any], orientation: str, pin_name: str) -> str:
    """Point a lab away from the transformed symbol body, not its origin.

    Embedded symbols may put a left-side input above the instance origin.  Using
    only ``abs(dx)`` versus ``abs(dy)`` then rotates the lab upward into visible
    device titles.  Normalising against the real drawing footprint preserves
    semantic left-input/right-output placement for those symbols.
    """

    matching = next((item for item in component["symbol_record"].get("pins", []) if str(item.get("name")) == str(pin_name)), None)
    if matching is None:
        raise CatalogError("Symbol pin vanished while orienting terminal", component=component.get("id"), pin=pin_name)
    dx, dy = _transform_pin(int(matching["x"]), int(matching["y"]), orientation)
    body = _symbol_body_bbox(component, orientation)
    center_x = (int(body["min_x"]) + int(body["max_x"])) / 2.0
    center_y = (int(body["min_y"]) + int(body["max_y"])) / 2.0
    half_width = max(1.0, (int(body["max_x"]) - int(body["min_x"])) / 2.0)
    half_height = max(1.0, (int(body["max_y"]) - int(body["min_y"])) / 2.0)
    x_score = abs((dx - center_x) / half_width)
    y_score = abs((dy - center_y) / half_height)
    if x_score >= y_score:
        return "N180" if dx <= center_x else "N0"
    return "N270" if dy <= center_y else "N90"


def _annotation_text(component: Mapping[str, Any], annotation: Mapping[str, Any]) -> str:
    name = str(annotation.get("name", ""))
    actual = str(annotation.get("value", ""))
    for key, value in component.get("properties", {}).items():
        if str(key).casefold() == name.casefold():
            actual = str(value)
            break
    if annotation.get("display_name"):
        return f"{name}={actual}"
    return actual


def _text_size(text: str) -> tuple[int, int]:
    rows = str(text).replace("\\n", "\n").splitlines() or [""]
    return (
        max(1, max(len(row) for row in rows)) * _TEXT_CHARACTER_WIDTH,
        max(1, len(rows)) * _TEXT_LINE_HEIGHT,
    )


def _explicit_annotation_bbox(annotation: Mapping[str, Any], text: str, orientation: str) -> dict[str, int]:
    width, height = _text_size(text)
    x = int(annotation["x"])
    y = int(annotation["y"])
    align = str(annotation.get("align", "")).casefold()
    if "left" in align:
        min_x = x
    elif "right" in align:
        min_x = x - width
    else:
        min_x = x - width // 2
    if "top" in align:
        min_y = y
    elif "base" in align or "bottom" in align:
        min_y = y - height
    else:
        min_y = y - height // 2
    return _transformed_bbox(
        {"min_x": min_x, "min_y": min_y, "max_x": min_x + width, "max_y": min_y + height},
        orientation,
    )


def _autopos_annotation_boxes(
    annotations: Iterable[Mapping[str, Any]],
    component: Mapping[str, Any],
    orientation: str,
    body: Mapping[str, int],
) -> list[dict[str, Any]]:
    angle = int(_normalize_orientation(orientation)[1:])
    grouped: dict[str, list[tuple[Mapping[str, Any], str]]] = {"left": [], "right": [], "top": [], "bottom": []}
    for annotation in annotations:
        if "x" in annotation and "y" in annotation and int(annotation.get("autopos", 0)) == 0:
            continue
        text = _annotation_text(component, annotation)
        raw_side = str(annotation.get("normal" if angle in {0, 180} else "rotated", "Right")).casefold()
        side = next((candidate for candidate in grouped if candidate in raw_side), "right")
        grouped[side].append((annotation, text))
    boxes: list[dict[str, Any]] = []
    for side, values in grouped.items():
        values.sort(key=lambda item: (int(item[0].get("order", 0)), str(item[0].get("name", ""))))
        cursor = 0
        for annotation, text in values:
            width, height = _text_size(text)
            if side == "right":
                min_x = int(body["max_x"]) + _AUTOPOS_GAP
                min_y = int(body["min_y"]) + cursor
                cursor += height + _AUTOPOS_STACK_GAP
            elif side == "left":
                min_x = int(body["min_x"]) - _AUTOPOS_GAP - width
                min_y = int(body["min_y"]) + cursor
                cursor += height + _AUTOPOS_STACK_GAP
            elif side == "top":
                min_x = (int(body["min_x"]) + int(body["max_x"]) - width) // 2
                min_y = int(body["min_y"]) - _AUTOPOS_GAP - height - cursor
                cursor += height + _AUTOPOS_STACK_GAP
            else:
                min_x = (int(body["min_x"]) + int(body["max_x"]) - width) // 2
                min_y = int(body["max_y"]) + _AUTOPOS_GAP + cursor
                cursor += height + _AUTOPOS_STACK_GAP
            boxes.append(
                {
                    "name": str(annotation.get("name", "")),
                    "text": text,
                    "placement": f"autopos:{side}",
                    "bbox": {"min_x": min_x, "min_y": min_y, "max_x": min_x + width, "max_y": min_y + height},
                }
            )
    return boxes


def _terminal_annotation_boxes(
    component: Mapping[str, Any],
    orientation: str,
) -> list[dict[str, Any]]:
    """Reserve the visible lab/ground geometry emitted at connected pins."""

    if str(component.get("kind", "")).casefold() == "ground":
        return []
    pin_records = {str(pin.get("name")): pin for pin in component["symbol_record"].get("pins", [])}
    records: list[dict[str, Any]] = []
    for pin_name, net in component.get("pins", {}).items():
        pin = pin_records.get(str(pin_name))
        if pin is None:
            continue
        dx, dy = _transform_pin(int(pin["x"]), int(pin["y"]), orientation)
        if str(net) in GROUND_NETS:
            bbox = {
                "min_x": dx - _TERMINAL_BODY_LENGTH,
                "min_y": dy,
                "max_x": dx + _TERMINAL_BODY_LENGTH,
                "max_y": dy + _GROUND_BODY_HEIGHT,
            }
            placement = "ground:N0"
        else:
            width, height = _text_size(str(net))
            term_orientation = _pin_term_orientation(component, orientation, str(pin_name))
            if term_orientation in {"N180", "N0"}:
                min_y = dy - height // 2
                if term_orientation == "N180":
                    min_x = dx - _TERMINAL_BODY_LENGTH - width
                    max_x = dx
                    placement = "terminal:N180"
                else:
                    min_x = dx
                    max_x = dx + _TERMINAL_BODY_LENGTH + width
                    placement = "terminal:N0"
                bbox = {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": min_y + height}
            else:
                min_x = dx - height // 2
                if term_orientation == "N270":
                    min_y = dy - _TERMINAL_BODY_LENGTH - width
                    max_y = dy
                    placement = "terminal:N270"
                else:
                    min_y = dy
                    max_y = dy + _TERMINAL_BODY_LENGTH + width
                    placement = "terminal:N90"
                bbox = {"min_x": min_x, "min_y": min_y, "max_x": min_x + height, "max_y": max_y}
        records.append(
            {
                "name": f"pin:{pin_name}",
                "text": str(net),
                "placement": placement,
                "bbox": bbox,
            }
        )
    return records


def _symbol_bbox(component: Mapping[str, Any], orientation: str, padding: int = 120) -> dict[str, int]:
    body = _symbol_body_bbox(component, orientation)
    annotation_records: list[dict[str, Any]] = []
    annotations = component["symbol_record"].get("visible_properties", [])
    if isinstance(annotations, list):
        for annotation in annotations:
            if not isinstance(annotation, Mapping) or "x" not in annotation or "y" not in annotation:
                continue
            if int(annotation.get("autopos", 0)) != 0:
                continue
            text = _annotation_text(component, annotation)
            annotation_records.append(
                {
                    "name": str(annotation.get("name", "")),
                    "text": text,
                    "placement": "explicit",
                    "bbox": _explicit_annotation_bbox(annotation, text, orientation),
                }
            )
        annotation_records.extend(_autopos_annotation_boxes(annotations, component, orientation, body))
    terminal_records = _terminal_annotation_boxes(component, orientation)
    xs = [int(body["min_x"]), int(body["max_x"])]
    ys = [int(body["min_y"]), int(body["max_y"])]
    for record in [*annotation_records, *terminal_records]:
        box = record["bbox"]
        xs.extend((int(box["min_x"]), int(box["max_x"])))
        ys.extend((int(box["min_y"]), int(box["max_y"])))
    min_x = min(xs) - padding
    min_y = min(ys) - padding
    max_x = max(xs) + padding
    max_y = max(ys) + padding
    return {
        "min_x": min_x,
        "min_y": min_y,
        "max_x": max_x,
        "max_y": max_y,
        "width": max_x - min_x,
        "height": max_y - min_y,
        "body_bbox": dict(body),
        "visible_annotations": annotation_records,
        "terminal_annotations": terminal_records,
    }


def _absolute_bbox(x: int, y: int, footprint: Mapping[str, int]) -> dict[str, int]:
    return {
        "min_x": x + int(footprint["min_x"]),
        "min_y": y + int(footprint["min_y"]),
        "max_x": x + int(footprint["max_x"]),
        "max_y": y + int(footprint["max_y"]),
        "width": int(footprint["width"]),
        "height": int(footprint["height"]),
    }


def _rectangles_conflict(left: Mapping[str, int], right: Mapping[str, int], horizontal: int, vertical: int) -> bool:
    return not (
        int(left["max_x"]) + horizontal <= int(right["min_x"])
        or int(right["max_x"]) + horizontal <= int(left["min_x"])
        or int(left["max_y"]) + vertical <= int(right["min_y"])
        or int(right["max_y"]) + vertical <= int(left["min_y"])
    )


def _snap(value: int, grid: int) -> int:
    return int(round(value / grid) * grid)


def _snap_up(value: int, grid: int) -> int:
    """Snap a required outward displacement without rounding it back inward."""

    return -(-int(value) // grid) * grid


_POWER_GROUPS = {"input", "power", "power_stage", "output"}
_CONTROL_GROUPS = {"reference", "feedback", "ripple", "control", "timing", "driver"}
_ANALYSIS_GROUPS = {"analysis", "observation", "probe", "probes"}


def _automatic_orientation(component: Mapping[str, Any], source: Mapping[str, Any], mode: str) -> str:
    raw_layout = source.get("layout", {}) if isinstance(source.get("layout"), Mapping) else {}
    if "orientation" in raw_layout or mode == "manual":
        return _normalize_orientation(raw_layout.get("orientation", "N0"))
    pins = component["symbol_record"].get("pins", [])
    if len(pins) != 2:
        return _normalize_orientation(component.get("preferred_orientation", "N0"))
    pin_by_name = {str(pin["name"]): pin for pin in pins}
    connected = source.get("pins", {}) if isinstance(source.get("pins"), Mapping) else {}
    candidates = ("N0", "N90", "N180", "N270")
    ground_pin = next((name for name, net in connected.items() if str(net) in GROUND_NETS and name in pin_by_name), None)
    other_pin = next((name for name in pin_by_name if name != ground_pin), None) if ground_pin else None
    if ground_pin and other_pin:
        return max(
            candidates,
            key=lambda item: _transform_pin(int(pin_by_name[ground_pin]["x"]), int(pin_by_name[ground_pin]["y"]), item)[1]
            - _transform_pin(int(pin_by_name[other_pin]["x"]), int(pin_by_name[other_pin]["y"]), item)[1],
        )
    if "P" in pin_by_name and "N" in pin_by_name:
        return max(
            candidates,
            key=lambda item: _transform_pin(int(pin_by_name["N"]["x"]), int(pin_by_name["N"]["y"]), item)[0]
            - _transform_pin(int(pin_by_name["P"]["x"]), int(pin_by_name["P"]["y"]), item)[0],
        )
    return _normalize_orientation(component.get("preferred_orientation", "N0"))


def _layout_band(group: str) -> int:
    lowered = group.casefold()
    if lowered in _POWER_GROUPS:
        return 0
    if lowered in _ANALYSIS_GROUPS:
        return 2
    if lowered in _CONTROL_GROUPS:
        return 1
    return 1


def _unit_bbox(members: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    items = [member["bbox"] for member in members]
    return {
        "min_x": min(int(item["min_x"]) for item in items),
        "min_y": min(int(item["min_y"]) for item in items),
        "max_x": max(int(item["max_x"]) for item in items),
        "max_y": max(int(item["max_y"]) for item in items),
    }


def _shift_members(members: list[dict[str, Any]], dx: int, dy: int) -> None:
    for member in members:
        member["x"] += dx
        member["y"] += dy
        member["bbox"] = _absolute_bbox(member["x"], member["y"], member["footprint"])


def _component_layout(
    circuit: Mapping[str, Any],
    components: list[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    layout = circuit.get("layout", {}) if isinstance(circuit.get("layout"), Mapping) else {}
    mode = str(layout.get("mode", "hybrid")).casefold()
    if mode not in {"hybrid", "auto", "manual"}:
        raise ValidationError("layout.mode must be hybrid, auto, or manual", mode=mode)
    grid = int(layout.get("grid", 120))
    if grid <= 0:
        raise ValidationError("layout.grid must be positive")
    clearance = layout.get("clearance", {}) if isinstance(layout.get("clearance"), Mapping) else {}
    horizontal = int(clearance.get("horizontal", layout.get("horizontal_clearance", 480)))
    vertical = int(clearance.get("vertical", layout.get("vertical_clearance", 360)))
    label_padding = int(layout.get("label_padding", 120))
    if horizontal < 0 or vertical < 0 or label_padding < 0:
        raise ValidationError("Layout clearances and label padding cannot be negative")
    origin = layout.get("origin", [-720, -360])
    if not isinstance(origin, list) or len(origin) != 2:
        raise ValidationError("layout.origin must contain [x, y]")
    band_spacing = int(layout.get("band_spacing", 1560))

    source_by_id = {str(item["id"]): item for item in circuit["components"]}
    resolved_by_id = {str(item["id"]): item for item in components}
    block_manifest = circuit.get("_block_manifest", {}) if isinstance(circuit.get("_block_manifest"), Mapping) else {}
    leaf_to_block = {
        str(leaf): str(block_id)
        for block_id, record in block_manifest.items()
        if isinstance(record, Mapping)
        for leaf in record.get("leaf_components", [])
    }
    groups: list[str] = [str(item) for item in layout.get("groups", [])]
    for source in circuit["components"]:
        group = str(source.get("group", "default"))
        if group not in groups:
            groups.append(group)
    group_counts = {group: 0 for group in groups}

    prepared: dict[str, dict[str, Any]] = {}
    for component_id, component in resolved_by_id.items():
        source = source_by_id[component_id]
        raw_layout = source.get("layout", {}) if isinstance(source.get("layout"), Mapping) else {}
        group = str(source.get("group", "default"))
        orientation = _automatic_orientation(component, source, mode)
        footprint = _symbol_bbox(component, orientation, padding=label_padding)
        block_id = leaf_to_block.get(component_id) or (str(source.get("block")) if source.get("block") else None)
        if mode == "manual" and block_id is None and not all(key in raw_layout for key in ("x", "y")):
            raise ValidationError("manual layout requires x/y for every standalone component", component=component_id)
        index = group_counts[group]
        group_counts[group] += 1
        group_index = groups.index(group)
        default_x = int(origin[0]) + group_index * 2160
        default_y = int(origin[1]) + _layout_band(group) * band_spacing + index * (footprint["height"] + vertical)
        requested_x = _snap(int(raw_layout.get("x", default_x)), grid)
        requested_y = _snap(int(raw_layout.get("y", default_y)), grid)
        prepared[component_id] = {
            "id": component_id,
            "x": requested_x,
            "y": requested_y,
            "requested_x": requested_x,
            "requested_y": requested_y,
            "orientation": orientation,
            "group": group,
            "block": block_id,
            "row": int(raw_layout.get("row", 0)),
            "col": int(raw_layout.get("col", 0)),
            "footprint": footprint,
            "bbox": _absolute_bbox(requested_x, requested_y, footprint),
            "explicit": "x" in raw_layout and "y" in raw_layout,
            "fixed": mode == "manual" or (mode == "hybrid" and block_id is None and "x" in raw_layout and "y" in raw_layout),
            "move_reasons": [],
            "placement_reason": "explicit_fixed" if "x" in raw_layout and "y" in raw_layout and block_id is None else "automatic_band_pack",
        }

    units: list[dict[str, Any]] = []
    block_members: dict[str, list[dict[str, Any]]] = {}
    for item in prepared.values():
        if item["block"]:
            block_members.setdefault(str(item["block"]), []).append(item)
        else:
            units.append({"id": item["id"], "members": [item], "fixed": item["fixed"], "block": None})
    for block_id, members in block_members.items():
        record = block_manifest.get(block_id, {}) if isinstance(block_manifest.get(block_id), Mapping) else {}
        block_layout = record.get("layout", {}) if isinstance(record.get("layout"), Mapping) else {}
        if mode == "manual" and not all(key in block_layout for key in ("x", "y")):
            raise ValidationError("manual layout requires x/y for every functional block", block=block_id)
        anchor_x = _snap(int(block_layout.get("x", min(item["requested_x"] for item in members))), grid)
        anchor_y = _snap(int(block_layout.get("y", min(item["requested_y"] for item in members))), grid)
        cells: dict[tuple[int, int], str] = {}
        for item in members:
            cell = (int(item["row"]), int(item["col"]))
            if cell in cells:
                raise ValidationError("Functional block leaves share one layout cell", block=block_id, first=cells[cell], second=item["id"], cell=list(cell))
            cells[cell] = str(item["id"])
        columns = sorted({int(item["col"]) for item in members})
        rows = sorted({int(item["row"]) for item in members})
        column_width = {column: max(item["footprint"]["width"] for item in members if int(item["col"]) == column) for column in columns}
        row_height = {row: max(item["footprint"]["height"] for item in members if int(item["row"]) == row) for row in rows}
        x_cursor: dict[int, int] = {}
        cursor = anchor_x
        for column in columns:
            x_cursor[column] = cursor
            cursor += column_width[column] + horizontal
        y_cursor: dict[int, int] = {}
        cursor = anchor_y
        for row in rows:
            y_cursor[row] = cursor
            cursor += row_height[row] + vertical
        for item in members:
            item["x"] = _snap(x_cursor[int(item["col"])] - item["footprint"]["min_x"], grid)
            item["y"] = _snap(y_cursor[int(item["row"])] - item["footprint"]["min_y"], grid)
            item["bbox"] = _absolute_bbox(item["x"], item["y"], item["footprint"])
            # A block anchor plus the footprint pack is the requested leaf
            # position.  Later shifts are collision-resolution moves.
            item["requested_x"] = item["x"]
            item["requested_y"] = item["y"]
            item["placement_reason"] = "block_footprint_pack"
        units.append({"id": block_id, "members": members, "fixed": mode == "manual", "block": block_id})

    fixed_units = [unit for unit in units if unit["fixed"]]
    moving_units = [unit for unit in units if not unit["fixed"]]
    occupied: list[dict[str, Any]] = []
    fixed_conflicts: list[dict[str, Any]] = []
    for unit in fixed_units:
        bbox = _unit_bbox(unit["members"])
        for conflict in occupied:
            if _rectangles_conflict(bbox, conflict["bbox"], horizontal, vertical):
                fixed_conflicts.append(
                    {"left": str(conflict["id"]), "right": str(unit["id"]), "left_bbox": conflict["bbox"], "right_bbox": bbox}
                )
        occupied.append({"id": unit["id"], "bbox": bbox})
    if fixed_conflicts:
        raise ValidationError("Fixed layout objects overlap", collisions=fixed_conflicts)
    unit_order = {id(unit): index for index, unit in enumerate(units)}
    for unit in sorted(moving_units, key=lambda item: (_layout_band(str(item["members"][0]["group"])), unit_order[id(item)])):
        moves = 0
        while True:
            bbox = _unit_bbox(unit["members"])
            conflicts = [other for other in occupied if _rectangles_conflict(bbox, other["bbox"], horizontal, vertical)]
            if not conflicts:
                break
            target_x = max(int(other["bbox"]["max_x"]) + horizontal for other in conflicts)
            _shift_members(unit["members"], _snap_up(target_x - bbox["min_x"], grid), 0)
            for member in unit["members"]:
                if "collision_resolution" not in member["move_reasons"]:
                    member["move_reasons"].append("collision_resolution")
            moves += 1
            if moves > len(units) + 8:
                raise ValidationError("Automatic layout could not resolve collisions", object=unit["id"], conflicts=[item["id"] for item in conflicts])
        occupied.append({"id": unit["id"], "bbox": _unit_bbox(unit["members"])})

    final_units = [{"id": item["id"], "bbox": _unit_bbox(item["members"])} for item in units]
    checked_pairs: list[list[str]] = []
    for index, left in enumerate(final_units):
        for right in final_units[index + 1 :]:
            checked_pairs.append([str(left["id"]), str(right["id"])])
            if _rectangles_conflict(left["bbox"], right["bbox"], horizontal, vertical):
                raise ValidationError("Layout collision remains after packing", left=left["id"], right=right["id"], left_bbox=left["bbox"], right_bbox=right["bbox"])

    placements: dict[str, dict[str, Any]] = {}
    for component_id, item in prepared.items():
        placements[component_id] = {
            "x": item["x"],
            "y": item["y"],
            "requested_x": item["requested_x"],
            "requested_y": item["requested_y"],
            "orientation": item["orientation"],
            "group": item["group"],
            "block": item["block"],
            "fixed": item["fixed"],
            "moved": item["x"] != item["requested_x"] or item["y"] != item["requested_y"],
            "requested_position": [item["requested_x"], item["requested_y"]],
            "final_position": [item["x"], item["y"]],
            "placement_reason": item["placement_reason"],
            "move_reason": list(item["move_reasons"]),
            "footprint": dict(item["footprint"]),
            "bbox": dict(item["bbox"]),
        }
    summary = {
        "mode": mode,
        "grid": grid,
        "clearance": {"horizontal": horizontal, "vertical": vertical},
        "label_padding": label_padding,
        "collisions": [],
        "objects": [{"id": item["id"], "bbox": item["bbox"]} for item in occupied],
        "collision_check": {"passed": True, "checked_pairs": checked_pairs},
    }
    return placements, summary


def _resolve_value(raw: Any, definition: Mapping[str, Any], parameters: Mapping[str, Any]) -> str:
    if definition.get("raw") or definition.get("value_type") in {"string", "enum"}:
        if definition.get("enum") and raw not in definition["enum"]:
            raise ValidationError("Property is outside its allowed enum", property=definition.get("native"), value=raw)
        if isinstance(raw, str) and "${" in raw:
            # Raw strings may interpolate only complete parameters; code-like expressions stay impossible.
            import re

            def substitute(match: re.Match[str]) -> str:
                name = match.group(1)
                if name not in parameters:
                    raise ValidationError("Unknown parameter in raw property", parameter=name)
                return format_simplis(parameters[name])

            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", substitute, raw)
        return str(raw)
    quantity = evaluate_expression(raw, parameters, definition.get("dimension", "dimensionless"))
    minimum = definition.get("min")
    maximum = definition.get("max")
    if minimum is not None and quantity.value < evaluate_expression(minimum, parameters, definition.get("dimension")).value:
        raise ValidationError("Property is below its catalog minimum", property=definition.get("native"), value=raw, minimum=minimum)
    if maximum is not None and quantity.value > evaluate_expression(maximum, parameters, definition.get("dimension")).value:
        raise ValidationError("Property is above its catalog maximum", property=definition.get("native"), value=raw, maximum=maximum)
    return format_simplis(quantity)


def _interpolate_raw(raw: Any, parameters: Mapping[str, Any]) -> str:
    """Allow complete declared-parameter references in catalog placement adapters."""

    if not isinstance(raw, str) or "${" not in raw:
        return str(raw)
    import re

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in parameters:
            raise ValidationError("Unknown parameter in placement property", parameter=name)
        return format_simplis(parameters[name])

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", substitute, raw)


def _resolve_component(
    component: Mapping[str, Any],
    catalog: Mapping[str, Any],
    installed: Mapping[str, Mapping[str, Any]],
    parameters: Mapping[str, Any],
    *,
    allow_pending: bool = False,
) -> dict[str, Any]:
    component_id = str(component["id"])
    kind = str(component["kind"])
    opaque = kind == "opaque_module"
    if opaque:
        native = component.get("native", {})
        if not isinstance(native, dict) or not native.get("symbol"):
            raise ValidationError("Opaque module cannot be rendered without native.symbol", component=component_id)
        symbol_name = str(native["symbol"])
        device = {"kind": kind, "symbol": {"name": symbol_name}, "pins": [{"name": name, "domain": "analog"} for name in component["pins"]], "properties": {}, "ideality": "opaque"}
    else:
        device = get_device(dict(catalog), kind, approved_only=not allow_pending)
        symbol_name = str(device.get("placement_symbol") or device["symbol"]["name"])
    symbol = installed.get(symbol_name)
    if symbol is None:
        raise CatalogError("Catalog placement symbol is not installed", component=component_id, symbol=symbol_name)
    installed_pins = {pin["name"]: pin for pin in symbol["pins"]}
    expected_pins = {pin["name"]: pin for pin in device["pins"]}
    actual_pins = component["pins"]
    unconnected_pins = {str(pin) for pin in component.get("unconnected_pins", [])}
    unknown = sorted(set(actual_pins) - set(expected_pins))
    unknown_unconnected = sorted(unconnected_pins - set(expected_pins))
    overlap = sorted(set(actual_pins) & unconnected_pins)
    missing = sorted(set(expected_pins) - set(actual_pins) - unconnected_pins)
    if unknown or unknown_unconnected or overlap or missing:
        raise ValidationError(
            "Component pins disagree with catalog",
            component=component_id,
            unknown=unknown,
            unknown_unconnected=unknown_unconnected,
            connected_and_unconnected=overlap,
            missing=missing,
        )
    absent = sorted(set(expected_pins) - set(installed_pins))
    if absent:
        raise CatalogError("Installed symbol pins disagree with catalog", component=component_id, symbol=symbol_name, missing=absent)
    properties: dict[str, str] = {}
    resolved_public: dict[str, str] = {}
    # Dynamic DIGI1/Logic-BB display symbols are deliberately property-light
    # in system.sxslb.  Their bound SIMPLIS template still requires <ref>, so
    # the placement adapter must supply REF even though the display symbol's
    # static property inventory does not advertise it.
    symbol_has_ref = any(str(name).casefold() == "ref" for name in symbol.get("properties", {}))
    if symbol_has_ref or device.get("placement_adapter") == "display_symbol_binding":
        properties["REF"] = str(component.get("ref", component_id))
    placement_properties = device.get("placement_properties", {})
    if not isinstance(placement_properties, dict):
        raise CatalogError("placement_properties must be a mapping", kind=kind)
    properties.update({str(name): _interpolate_raw(value, parameters) for name, value in placement_properties.items()})
    definitions = device.get("properties", {})
    source_properties = component.get("properties", {})
    if not isinstance(source_properties, dict):
        raise ValidationError("Component properties must be a mapping", component=component_id)
    for public_name, definition in definitions.items():
        if "default" in definition:
            resolved = _resolve_value(definition["default"], definition, parameters)
            resolved_public[str(public_name)] = resolved
            if not definition.get("virtual"):
                properties[str(definition.get("native", public_name))] = resolved
    for property_name, raw in source_properties.items():
        match = find_property(device, str(property_name)) if not opaque else None
        if opaque:
            # Imported opaque modules are deliberately not claimed ideal.  Their
            # unchanged native attributes may be preserved for a boundary-only
            # round trip, but cannot be parameterized or type-checked.
            if isinstance(raw, str) and "${" in raw:
                raise ValidationError("Opaque module properties cannot be parameterized", component=component_id, property=property_name)
            properties[str(property_name)] = str(raw)
            continue
        if match is None:
            raise ValidationError("Component property is not catalog-approved", component=component_id, property=property_name)
        public_name, definition = match
        resolved = _resolve_value(raw, definition, parameters)
        resolved_public[str(public_name)] = resolved
        if not definition.get("virtual"):
            properties[str(definition.get("native", public_name))] = resolved
    for public_name, definition in definitions.items():
        if definition.get("required") and public_name not in resolved_public and str(definition.get("native", public_name)) not in properties:
            raise ValidationError("Required catalog property is missing", component=component_id, property=public_name)
    if device.get("property_encoder") == "vpwlr_ideal_diode":
        try:
            ron = float(resolved_public["RON"])
            roff = float(resolved_public["ROFF"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("VPWLR idealized diode requires numeric RON and ROFF", component=component_id) from exc
        if ron <= 0 or roff <= 0:
            raise ValidationError("VPWLR idealized diode RON and ROFF must be positive", component=component_id)
        reverse_voltage = -1.0
        forward_voltage = 1.0
        properties["VALUE"] = " ".join(
            (
                "NSEG=2",
                f"X0={format(reverse_voltage, '.12g')}",
                f"Y0={format(reverse_voltage / roff, '.12g')}",
                "X1=0",
                "Y1=0",
                f"X2={format(forward_voltage, '.12g')}",
                f"Y2={format(forward_voltage / ron, '.12g')}",
            )
        )
    if device.get("property_encoder") == "simplis_vc_switch_vars":
        try:
            ron = float(resolved_public["RON"])
            roff = float(resolved_public["ROFF"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("SIMPLIS voltage-controlled switch requires numeric RON and ROFF", component=component_id) from exc
        if ron <= 0 or roff <= 0:
            raise ValidationError("SIMPLIS voltage-controlled switch RON and ROFF must be positive", component=component_id)
        initial = str(resolved_public.get("IC", "Open")).strip().casefold()
        if initial == "open":
            encoded_initial = "OPEN"
        elif initial in {"close", "closed"}:
            encoded_initial = "CLOSE"
        else:  # The catalog enum should reject this first; retain a local fail-closed guard.
            raise ValidationError("Unsupported SIMPLIS voltage-controlled switch initial state", component=component_id, value=initial)
        logic = str(resolved_public.get("LOGIC", "POS")).strip().upper()
        if logic not in {"POS", "NEG"}:
            raise ValidationError("Unsupported SIMPLIS voltage-controlled switch logic polarity", component=component_id, value=logic)
        properties["VALUE"] = " ".join(
            (
                f"ROFF={resolved_public['ROFF']}",
                f"RON={resolved_public['RON']}",
                f"THRESHOLD={resolved_public['THRESHOLD']}",
                f"HYSTWD={resolved_public['HYSTWD']}",
                f"IC='{encoded_initial}'",
                f"LOGIC='{logic}'",
            )
        )
    if device.get("property_encoder") == "reactive_value_ic":
        value = resolved_public.get("VALUE")
        if value is None:
            raise ValidationError("Reactive IC encoder requires VALUE", component=component_id)
        initial = resolved_public.get("IC")
        properties["VALUE"] = value if initial is None else f"{value} IC={initial}"
    pin_domains = {pin["name"]: str(pin.get("domain", "analog")) for pin in device["pins"]}
    return {
        "id": component_id,
        "ref": str(component.get("ref", component_id)),
        "kind": kind,
        "symbol": symbol_name,
        "symbol_record": symbol,
        "properties": properties,
        "pins": {str(pin): str(net) for pin, net in actual_pins.items()},
        "unconnected_pins": sorted(unconnected_pins),
        "pin_domains": pin_domains,
        "opaque": opaque,
        "preferred_orientation": str(device.get("preferred_orientation", "N0")),
        "placement_adapter": str(device.get("placement_adapter", "library_symbol")),
        "netlist_presence": str(device.get("netlist_presence", "instance")),
    }


def _validate_domains(components: list[Mapping[str, Any]]) -> None:
    net_domains: dict[str, set[str]] = {}
    for component in components:
        for pin, net in component["pins"].items():
            net_domains.setdefault(net, set()).add(component["pin_domains"].get(pin, "analog"))
    for net, domains in net_domains.items():
        if "analog" in domains and "digital" in domains:
            raise ValidationError(
                "Analog and digital pins share a net without a catalog boundary adapter",
                net=net,
                domains=sorted(domains),
            )


def _pin_location(component: Mapping[str, Any], placement: Mapping[str, Any], pin_name: str) -> tuple[int, int, int, int]:
    matching = next((item for item in component["symbol_record"]["pins"] if item["name"] == pin_name), None)
    if matching is None:
        raise CatalogError("Symbol pin vanished while rendering", component=component["id"], pin=pin_name)
    dx, dy = _transform_pin(int(matching["x"]), int(matching["y"]), str(placement["orientation"]))
    return int(placement["x"]) + dx, int(placement["y"]) + dy, dx, dy


Point = tuple[int, int]
Segment = tuple[Point, Point]


def _path_segments(points: Iterable[Point]) -> list[Segment]:
    compact: list[Point] = []
    for point in points:
        if not compact or point != compact[-1]:
            compact.append(point)
    return [(left, right) for left, right in zip(compact, compact[1:]) if left != right]


def _segment_contains_point(segment: Segment, point: Point) -> bool:
    (x1, y1), (x2, y2) = segment
    x, y = point
    if x1 == x2:
        return x == x1 and min(y1, y2) <= y <= max(y1, y2)
    if y1 == y2:
        return y == y1 and min(x1, x2) <= x <= max(x1, x2)
    raise ValidationError("Routing segment is not Manhattan", segment=[list(segment[0]), list(segment[1])])


def _segments_intersect(left: Segment, right: Segment) -> bool:
    (ax1, ay1), (ax2, ay2) = left
    (bx1, by1), (bx2, by2) = right
    left_vertical = ax1 == ax2
    right_vertical = bx1 == bx2
    if left_vertical and right_vertical:
        return ax1 == bx1 and max(min(ay1, ay2), min(by1, by2)) <= min(max(ay1, ay2), max(by1, by2))
    if not left_vertical and not right_vertical:
        return ay1 == by1 and max(min(ax1, ax2), min(bx1, bx2)) <= min(max(ax1, ax2), max(bx1, bx2))
    vertical, horizontal = (left, right) if left_vertical else (right, left)
    (vx1, vy1), (_vx2, vy2) = vertical
    (hx1, hy1), (hx2, _hy2) = horizontal
    return min(hx1, hx2) <= vx1 <= max(hx1, hx2) and min(vy1, vy2) <= hy1 <= max(vy1, vy2)


def _route_pair(
    start: Point,
    end: Point,
    *,
    other_net_points: set[Point],
    occupied_segments: list[Segment],
    grid: int = 120,
) -> list[Segment] | None:
    """Choose the shortest non-merging Manhattan path between two real pins."""

    candidate_points: list[list[Point]] = []
    if start[0] == end[0] or start[1] == end[1]:
        candidate_points.append([start, end])
    candidate_points.extend(
        (
            [start, (end[0], start[1]), end],
            [start, (start[0], end[1]), end],
        )
    )
    mid_x = _snap((start[0] + end[0]) // 2, grid)
    mid_y = _snap((start[1] + end[1]) // 2, grid)
    x_tracks = [
        mid_x,
        min(start[0], end[0]) - grid,
        max(start[0], end[0]) + grid,
        mid_x - grid,
        mid_x + grid,
        min(start[0], end[0]) - 2 * grid,
        max(start[0], end[0]) + 2 * grid,
    ]
    y_tracks = [
        mid_y,
        min(start[1], end[1]) - grid,
        max(start[1], end[1]) + grid,
        mid_y - grid,
        mid_y + grid,
        min(start[1], end[1]) - 2 * grid,
        max(start[1], end[1]) + 2 * grid,
    ]
    candidate_points.extend([start, (track, start[1]), (track, end[1]), end] for track in x_tracks)
    candidate_points.extend([start, (start[0], track), (end[0], track), end] for track in y_tracks)

    candidates: list[list[Segment]] = []
    seen: set[tuple[Segment, ...]] = set()
    for points in candidate_points:
        segments = _path_segments(points)
        key = tuple(segments)
        if not segments or key in seen:
            continue
        seen.add(key)
        candidates.append(segments)
    candidates.sort(
        key=lambda segments: (
            sum(abs(right[0] - left[0]) + abs(right[1] - left[1]) for left, right in segments),
            len(segments),
            segments,
        )
    )
    for segments in candidates:
        if any(_segment_contains_point(segment, point) for segment in segments for point in other_net_points):
            continue
        if any(_segments_intersect(segment, occupied) for segment in segments for occupied in occupied_segments):
            continue
        return segments
    return None


def _routing_plan(
    components: list[Mapping[str, Any]],
    placements: Mapping[str, Mapping[str, Any]],
    max_wire_length: int = 720,
    block_wire_length: int = 2880,
) -> dict[str, Any]:
    endpoints: dict[str, list[dict[str, Any]]] = {}
    for component in components:
        placement = placements[component["id"]]
        for pin_name, net in component["pins"].items():
            x, y, dx, dy = _pin_location(component, placement, pin_name)
            endpoints.setdefault(net, []).append(
                {
                    "component": component["id"],
                    "pin": pin_name,
                    "group": placement["group"],
                    "block": placement.get("block"),
                    "x": x,
                    "y": y,
                    "dx": dx,
                    "dy": dy,
                    "term_orientation": _pin_term_orientation(component, str(placement["orientation"]), pin_name),
                }
            )
    locations: dict[tuple[int, int], list[tuple[str, dict[str, Any]]]] = {}
    for net, items in endpoints.items():
        for item in items:
            locations.setdefault((item["x"], item["y"]), []).append((net, item))
    for location, attached in locations.items():
        nets = {net for net, _item in attached}
        if len(nets) > 1:
            raise ValidationError(
                "Pins from distinct nets occupy the same schematic coordinate",
                location=list(location),
                endpoints=[f"{item['component']}.{item['pin']}={net}" for net, item in attached],
            )
    local_candidates: dict[str, list[dict[str, Any]]] = {}
    labeled: dict[str, list[dict[str, Any]]] = {}
    for net, items in endpoints.items():
        groups = {item["group"] for item in items}
        blocks = {item.get("block") for item in items}
        span = max((abs(a["x"] - b["x"]) + abs(a["y"] - b["y"]) for a in items for b in items), default=0)
        collinear = len({item["x"] for item in items}) == 1 or len({item["y"] for item in items}) == 1
        same_functional_block = len(blocks) == 1 and None not in blocks
        short_standalone = span <= max_wire_length and collinear
        short_block_connection = same_functional_block and span <= block_wire_length
        if net not in GROUND_NETS and len(items) >= 2 and len(groups) == 1 and (short_standalone or short_block_connection):
            local_candidates[net] = items
        else:
            labeled[net] = items
    all_points_by_net = {
        net: {(int(item["x"]), int(item["y"])) for item in items}
        for net, items in endpoints.items()
    }
    local: dict[str, list[dict[str, Any]]] = {}
    local_segments: dict[str, list[Segment]] = {}
    occupied_segments: list[Segment] = []
    for net, items in local_candidates.items():
        other_points = set().union(*(points for other_net, points in all_points_by_net.items() if other_net != net))
        proposed: list[Segment] = []
        anchor = items[0]
        for endpoint in items[1:]:
            routed = _route_pair(
                (int(anchor["x"]), int(anchor["y"])),
                (int(endpoint["x"]), int(endpoint["y"])),
                other_net_points=other_points,
                occupied_segments=[*occupied_segments, *proposed],
            )
            if routed is None:
                proposed = []
                break
            proposed.extend(routed)
            anchor = endpoint
        if not proposed:
            labeled[net] = items
            continue
        local[net] = items
        local_segments[net] = proposed
        occupied_segments.extend(proposed)
    return {"local_wires": local, "local_wire_segments": local_segments, "labeled": labeled}


def _build_script(
    components: list[Mapping[str, Any]],
    placements: Mapping[str, Mapping[str, Any]],
    paths: Mapping[str, Path],
    design_name: str,
    *,
    bootstrap_schematic: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    lines = [
        "Set EchoOn",
        # 8.4 can receive the /s startup command before the schematic GUI has
        # completed phase-3 initialisation.  A short documented Sleep prevents
        # NewSchem from wedging in that startup-only race.
        "Let v2_startup_settle = Sleep(3)",
        f'RedirectMessages dup "{paths["message_log"]}"',
        f"Let v2_status = OpenEchoFile('{paths['status']}', 'w')",
        "Echo script_started=true",
        "Let v2_close = CloseEchoFile()",
    ]
    if bootstrap_schematic is None:
        # Keep the startup script on the active document. On SIMetrix 8.4,
        # /newWindow can leave the command context on an untitled parent
        # window and block before the first SaveAs without emitting a message.
        lines.append(f"NewSchem /simulator SIMPLIS {_quote(design_name)}")
    else:
        lines.append(f'OpenSchem /cd "{bootstrap_schematic}"')
    for component in components:
        placement = placements[component["id"]]
        lines.append("Unselect")
        lines.append(
            " ".join(
                [
                    "Inst",
                    "/select",
                    "/loc",
                    str(placement["x"]),
                    str(placement["y"]),
                    _script_orientation(str(placement["orientation"])),
                    component["symbol"],
                ]
            )
        )
        for name, value in component["properties"].items():
            lines.append(f"Prop /hideNew {_quote(name)} {_quote(value)}")
        lines.append("Unselect")
    routing = _routing_plan(components, placements)
    local_endpoint_keys = {
        (item["component"], item["pin"])
        for items in routing["local_wires"].values()
        for item in items
    }
    for component in components:
        placement = placements[component["id"]]
        for pin_name, net in component["pins"].items():
            x, y, dx, dy = _pin_location(component, placement, pin_name)
            if component["kind"] == "ground":
                continue
            if net in GROUND_NETS:
                lines.append(f"Inst /loc {x} {y} 0 gnd")
            elif (component["id"], pin_name) not in local_endpoint_keys:
                term_orientation = _pin_term_orientation(component, str(placement["orientation"]), pin_name)
                lines.append(f"Inst /loc {x} {y} {_script_orientation(term_orientation)} term VALUE {_quote(net)}")
    for net, items in routing["local_wires"].items():
        anchor = items[0]
        # Preserve the semantic net name in the generated netlist even when
        # SIMetrix owns the short local wire geometry.
        lines.append(
            f"Inst /loc {anchor['x']} {anchor['y']} {_script_orientation(str(anchor['term_orientation']))} term VALUE {_quote(net)}"
        )
        for (x1, y1), (x2, y2) in routing["local_wire_segments"][net]:
            # Every segment is created by SIMetrix.  The planner rejects paths
            # through another net's pin or an already planned wire; the verifier
            # still requires saved branch attachment evidence on both ends.
            lines.append(f"Wire /loc {x1} {y1} {x2} {y2}")
    lines.extend(
        [
            f'SaveAs /force "{paths["schematic"]}"',
            f'Netlist /simplis "{paths["netlist"]}"',
            f'SaveAs /force "{paths["schematic"]}"',
            f"Let v2_status = OpenEchoFile('{paths['status']}', 'a')",
            "Echo netlist_completed=true",
            # The watchdog treats completion_token as terminal.  It must be
            # the final status write or it can stop the process before the
            # stage-specific evidence reaches disk.
            "Echo completion_token=true",
            "Let v2_close = CloseEchoFile()",
            "RedirectMessages flush",
            "RedirectMessages off",
            "CloseSchem",
            "Quit",
            "",
        ]
    )
    summary = {
        "mode": "block_local_hybrid",
        "local_wires": {net: [f"{item['component']}.{item['pin']}" for item in items] for net, items in routing["local_wires"].items()},
        "endpoint_coordinates": {
            f"{item['component']}.{item['pin']}": [item["x"], item["y"]]
            for items in routing["local_wires"].values()
            for item in items
        },
        "local_wire_segments": {
            net: [[list(left), list(right)] for left, right in routing["local_wire_segments"][net]]
            for net in routing["local_wires"]
        },
        "labeled_networks": sorted(routing["labeled"]),
    }
    return "\n".join(lines), summary


def _write_embedded_host(records: Iterable[Mapping[str, Any]], path: Path) -> Path:
    """Create the smallest openable host for hash-locked embedded symbols."""

    unique: dict[str, Mapping[str, Any]] = {}
    for record in records:
        unique.setdefault(str(record["name"]), record)
    lines = ["SIMetrixFile type=schematic format=1.0 revision=7", ".Component", ".Schematic", ".SymbolLibrary"]
    for name in sorted(unique):
        definition = unique[name].get("definition_lines")
        if not isinstance(definition, list) or not definition:
            raise CatalogError("Embedded symbol record lacks its exact definition", symbol=name)
        lines.extend(str(item) for item in definition)
    lines.extend((".EndSymbolLibrary", "View x=0 y=0 zoom=7 snapgrid=120", 'SimulatorMode value="Simplis"', ".EndSchematic", ".EndComponent", ""))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def compile_circuit(
    circuit_path: str | Path,
    out_dir: str | Path,
    parameter_overrides: Mapping[str, Any] | None = None,
    catalog_path: str | Path | None = None,
    *,
    catalog_proof_kinds: Iterable[str] = (),
) -> dict[str, Any]:
    """Compile a validated v2 circuit without launching SIMetrix."""

    source_path = Path(circuit_path).resolve()
    circuit = load_circuit(source_path)
    catalog_source = _resolve_catalog_path(circuit, source_path, catalog_path)
    catalog = load_catalog(catalog_source)
    proof_kinds = {str(kind) for kind in catalog_proof_kinds}
    for kind in sorted(proof_kinds):
        device = get_device(catalog, kind, approved_only=False)
        if device.get("approval") != "pending":
            raise CatalogError(
                "Catalog proof mode only accepts explicitly pending devices",
                kind=kind,
                approval=device.get("approval"),
            )
    requested_fingerprint = circuit.get("catalog_lock", {}).get("fingerprint")
    if requested_fingerprint and requested_fingerprint != catalog["fingerprint"]:
        raise CatalogError("Circuit catalog fingerprint is stale", expected=requested_fingerprint, actual=catalog["fingerprint"])
    parameters = resolve_parameters(circuit, parameter_overrides)
    installed = parse_symbol_libraries(_runtime_symbol_dir(catalog))
    embedded_records: dict[str, dict[str, Any]] = {}
    for device in catalog.get("devices", []):
        if device.get("placement_adapter") != "embedded_clone":
            continue
        symbol_definition = device.get("symbol", {})
        source_value = symbol_definition.get("source")
        if not source_value:
            raise CatalogError("Embedded-clone catalog device lacks symbol.source", kind=device.get("kind"))
        source = Path(str(source_value))
        if not source.is_absolute():
            source = catalog_source.parent / source
        record = load_embedded_symbol(
            source,
            str(symbol_definition["name"]),
            expected_sha256=symbol_definition.get("source_sha256") or symbol_definition.get("sha256"),
        )
        installed[str(symbol_definition["name"])] = record
        embedded_records[str(symbol_definition["name"])] = record
    used_kinds = {str(component["kind"]) for component in circuit["components"]}
    unused_proof_kinds = sorted(proof_kinds - used_kinds)
    if unused_proof_kinds:
        raise CatalogError("Catalog proof kind is absent from the circuit", kinds=unused_proof_kinds)
    components = [
        _resolve_component(
            component,
            catalog,
            installed,
            parameters,
            allow_pending=str(component["kind"]) in proof_kinds,
        )
        for component in circuit["components"]
    ]
    _validate_domains(components)
    placements, layout_evidence = _component_layout(circuit, components)
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    design_name = str(circuit["design"]["name"])
    paths = {
        "script": root / "create_and_netlist.sxscr",
        "schematic": root / f"{design_name}.sxsch",
        "netlist": root / f"{design_name}.net",
        "manifest": root / "build-manifest.json",
        "message_log": root / "create-message.log",
        "status": root / "create-status.txt",
    }
    used_embedded = [embedded_records[component["symbol"]] for component in components if component["symbol"] in embedded_records]
    bootstrap: Path | None = None
    if used_embedded:
        bootstrap = _write_embedded_host(used_embedded, root / "embedded-symbol-host.sxsch")
        paths["embedded_symbol_host"] = bootstrap
    script, routing = _build_script(components, placements, paths, design_name, bootstrap_schematic=bootstrap)
    paths["script"].write_text(script, encoding="utf-8")
    opaque_modules = [component["id"] for component in components if component["opaque"]]
    manifest = {
        "schema_version": BUILD_MANIFEST_SCHEMA_VERSION,
        "manifest_path": str(paths["manifest"]),
        "build_dir": str(root),
        "source_circuit": str(source_path),
        "catalog": {"path": str(catalog_source), "fingerprint": catalog["fingerprint"]},
        "artifacts": {key: str(value) for key, value in paths.items() if key != "manifest"},
        "expected": {
            "components": [
                {
                    "id": component["id"],
                    "ref": component["ref"],
                    "kind": component["kind"],
                    "symbol": component["symbol"],
                    "pins": component["pins"],
                    "unconnected_pins": component["unconnected_pins"],
                    "netlist_presence": component["netlist_presence"],
                    "properties": component["properties"],
                    "layout": placements[component["id"]],
                }
                for component in components
            ],
            "nets": {net: details["endpoints"] for net, details in circuit["nets"].items()},
            "blocks": circuit.get("_block_manifest", {}),
            "routing": routing,
            "layout": layout_evidence,
            "embedded_symbols": [
                {
                    "name": name,
                    "source": record.get("source"),
                    "source_sha256": record.get("source_sha256"),
                    "bbox": record.get("bbox"),
                }
                for name, record in sorted(embedded_records.items())
                if any(component["symbol"] == name for component in components)
            ],
        },
        "parameters": {
            name: {"value": value.value, "dimension": value.dimension, "simplis": format_simplis(value)}
            for name, value in parameters.items()
        },
        "classification": {
            "static_valid": True,
            "fully_ideal": not proof_kinds and not opaque_modules and is_fully_ideal(catalog, [component["kind"] for component in components]),
            "opaque_modules": opaque_modules,
            "catalog_proof_only": bool(proof_kinds),
            "pending_devices": sorted(proof_kinds),
        },
        "script_sha256": sha256_file(paths["script"]),
    }
    write_json(paths["manifest"], manifest)
    return manifest
