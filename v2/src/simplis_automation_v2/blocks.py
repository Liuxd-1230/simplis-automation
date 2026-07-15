"""Deterministic expansion of functional circuit blocks into leaf devices.

Blocks are a convenience layer only: the compiler, catalog checks, netlist and
manifest always operate on the expanded leaf graph.  This keeps every claimed
connection reviewable and avoids hiding controller behavior in opaque models.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import re
from typing import Any, Callable, Iterable, Mapping

from .errors import ValidationError


# Leaf ids stay fully descriptive in the manifest.  The GUI reference is kept
# compact so a functional block remains readable at a normal editing zoom.
_REF_SUFFIXES = {
    "hs_diode": "D_HS_BODY",
    "ls_diode": "D_LS_BODY",
    "top": "RT",
    "bottom": "RB",
    "ac_injection": "AC",
    "bode": "BP",
    "scale": "E",
    "center": "BIAS",
    "filter_r": "R",
    "filter_c": "C",
    "restore_r": "RREST",
    "timer": "1S",
    "edge_inverter": "INV",
    "fall_delay": "DLY",
    "falling_edge_and": "AND",
    "comparator": "CMP",
    "latch": "SR",
    "buffer": "BUF",
    "hs_delay": "DLYH",
    "ls_delay": "DLYL",
    "gain": "E",
    "gm": "G",
}

_INTERNAL_BLOCK_PREFIXES = {
    "control": "CTRL",
    "deadtime": "DT",
    "feedback": "FB",
    "ripple": "RIP",
    "ton": "TON",
}
_INTERNAL_NET_SUFFIXES = {
    "buffered": "BUF",
    "divider": "DIV",
    "dsch": "DSCH",
    "inverted": "INV",
    "ramp": "RAMP",
    "ripple_drive": "DRV",
    "ripple_base": "BASE",
    "set": "SET",
    "ton_fall_delayed": "DLY",
    "ton_inverted": "INV",
}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must be a mapping")
    return dict(value)


def _net(block: Mapping[str, Any], name: str, *, optional: bool = False) -> str:
    ports = _mapping(block.get("ports", {}), f"block {block.get('id')}.ports")
    value = ports.get(name)
    if value is None and optional:
        return "0"
    if not isinstance(value, str) or not value:
        raise ValidationError("Functional block is missing a required port", block=block.get("id"), port=name)
    return value


def _internal(block_id: str, name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(block_id)).strip("_").upper() or "BLK"
    prefix = _INTERNAL_BLOCK_PREFIXES.get(str(block_id).casefold(), cleaned)
    if len(prefix) > 8:
        prefix = f"{prefix[:4]}{sha256(prefix.encode('utf-8')).hexdigest()[:3].upper()}"
    suffix = _INTERNAL_NET_SUFFIXES.get(str(name).casefold(), re.sub(r"[^A-Za-z0-9]+", "_", str(name)).strip("_").upper())
    return f"{prefix}_{suffix}"


def _leaf(
    block: Mapping[str, Any],
    suffix: str,
    kind: str,
    pins: Mapping[str, str],
    properties: Mapping[str, Any] | None = None,
    *,
    row: int = 0,
    col: int = 0,
    orientation: str | None = None,
    unconnected_pins: Iterable[str] = (),
) -> dict[str, Any]:
    block_id = str(block["id"])
    devices = block.get("devices", {}) if isinstance(block.get("devices"), Mapping) else {}
    chosen_kind = str(devices.get(suffix, kind))
    compact_suffix = _REF_SUFFIXES.get(suffix, suffix.upper())
    layout: dict[str, Any] = {"row": row, "col": col}
    if orientation is not None:
        layout["orientation"] = orientation
    leaf = {
        "id": f"{block_id}__{suffix}",
        "ref": f"{str(block.get('ref', block_id)).upper()}_{compact_suffix}",
        "kind": chosen_kind,
        "properties": dict(properties or {}),
        "pins": dict(pins),
        "group": str(block.get("group", block_id)),
        "block": block_id,
        "layout": layout,
    }
    unconnected = [str(pin) for pin in unconnected_pins]
    if unconnected:
        leaf["unconnected_pins"] = unconnected
    return leaf


def _parameters(block: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(block.get("parameters", {}), f"block {block.get('id')}.parameters")


def _half_bridge(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = _parameters(block)
    common = {
        "RON": p.get("ron", "1mohm"),
        "ROFF": p.get("roff", "100Mohm"),
        "THRESHOLD": p.get("threshold", "2.5V"),
        "HYSTWD": p.get("hysteresis", "10mV"),
    }
    return [
        _leaf(block, "hs", "voltage_controlled_switch", {"P": _net(block, "VIN"), "N": _net(block, "SW"), "CP": _net(block, "PWM_HS"), "CN": _net(block, "RTN", optional=True)}, common),
        _leaf(block, "hs_diode", "idealized_diode", {"P": _net(block, "SW"), "N": _net(block, "VIN")}, {"RON": p.get("diode_ron", "1mohm"), "ROFF": p.get("diode_roff", "100Mohm")}, col=1),
        _leaf(block, "ls", "voltage_controlled_switch", {"P": _net(block, "RTN", optional=True), "N": _net(block, "SW"), "CP": _net(block, "PWM_LS"), "CN": _net(block, "RTN", optional=True)}, common, row=1),
        _leaf(block, "ls_diode", "idealized_diode", {"P": _net(block, "RTN", optional=True), "N": _net(block, "SW")}, {"RON": p.get("diode_ron", "1mohm"), "ROFF": p.get("diode_roff", "100Mohm")}, row=1, col=1),
    ]


def _feedback(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = _parameters(block)
    ports = _mapping(block.get("ports", {}), f"block {block.get('id')}.ports")
    divider = str(ports.get("DIVIDER") or _internal(str(block["id"]), "divider"))
    return [
        _leaf(block, "top", "resistor", {"P": _net(block, "VOUT"), "N": divider}, {"VALUE": p.get("rtop", "90kohm")}),
        _leaf(block, "bottom", "resistor", {"P": divider, "N": _net(block, "RTN", optional=True)}, {"VALUE": p.get("rbot", "10kohm")}, row=1),
        _leaf(block, "ac_injection", "ac_injection_source", {"P": _net(block, "FB"), "N": divider}, {"VALUE": p.get("ac_value", "AC 1")}, col=1),
        # Bode_Probe2's visible TEXT is protected in SIMetrix 8.4. Keep the
        # installed =OUT/IN text and define the signed complex response in the
        # experiment contract instead of trying to edit the symbol property.
        _leaf(block, "bode", "bode_probe", {"OUT": _net(block, "FB"), "IN": divider}, row=1, col=1),
    ]


def _synthetic_ripple(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = _parameters(block)
    drive = _internal(str(block["id"]), "ripple_drive")
    base = _internal(str(block["id"]), "ripple_base")
    return [
        # Bias the ripple common mode so its falling valley, rather than its
        # cycle average, equals FB.  This preserves the nominal divider
        # reference while removing the predictable half-ripple DC error.
        _leaf(
            block,
            "center",
            "dc_voltage_source",
            {"P": base, "N": _net(block, "FB")},
            {"VALUE": p.get("offset", "0V")},
            row=1,
        ),
        _leaf(
            block,
            "scale",
            "vcvs",
            {"P": drive, "N": base, "CP": _net(block, "SW"), "CN": _net(block, "VOUT")},
            {"VALUE": p.get("gain", "0.1")},
        ),
        # Integrate the scaled inductor voltage (SW - VOUT) around the FB
        # common-mode level. Its cycle average is zero at volt-second balance,
        # so the comparator sees FB plus triangular synthetic ripple instead of
        # an edge-coupled square wave that immediately retriggers the latch.
        _leaf(block, "filter_r", "resistor", {"P": drive, "N": _net(block, "RIPPLE")}, {"VALUE": p.get("ripple_r", "100kohm")}, col=1),
        _leaf(
            block,
            "filter_c",
            "capacitor",
            {"P": _net(block, "RIPPLE"), "N": base},
            {"VALUE": p.get("ripple_c", "220pF"), "IC": p.get("initial_condition", "0V")},
            row=1,
            col=1,
        ),
        # Keep the switching-frequency path capacitive while giving the
        # ripple state an independent low-frequency return to its base.
        # This prevents a load transient from leaving the integrator biased
        # for the full drive-R times C time constant.
        _leaf(
            block,
            "restore_r",
            "resistor",
            {"P": _net(block, "RIPPLE"), "N": base},
            {"VALUE": p.get("restore_r", "100kohm")},
            row=2,
            col=1,
        ),
    ]


def _adaptive_ton(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = _parameters(block)
    ton = p.get("ton", "${ton_nominal}")
    ton_out = _net(block, "TON_OUT")
    ton_done = _net(block, "TON_DONE")
    inverted = _internal(str(block["id"]), "ton_inverted")
    delayed = _internal(str(block["id"]), "ton_fall_delayed")
    return [
        _leaf(
            block,
            "timer",
            "native_oneshot",
            {
                "IN": _net(block, "TRIG"),
                "RTN": _net(block, "RTN", optional=True),
                "OUT": ton_out,
                "DSCH": _internal(str(block["id"]), "dsch"),
                "RAMP": _internal(str(block["id"]), "ramp"),
            },
            {"TH": ton},
        ),
        _leaf(
            block,
            "edge_inverter",
            "digital_buffer_grounded",
            {"OUT_BAR": inverted, "IN1": ton_out},
            {"DELAY": p.get("logic_delay", "2ps")},
            col=1,
            unconnected_pins=("OUT",),
        ),
        _leaf(block, "fall_delay", "asymmetric_delay_grounded", {"OUT": delayed, "IN1": ton_out}, {"RISE_DELAY": p.get("logic_delay", "2ps"), "FALL_DELAY": p.get("reset_pulse", "5ns")}, row=1, col=1),
        _leaf(
            block,
            "falling_edge_and",
            "digital_and2_grounded",
            {"OUT": ton_done, "RTN": _net(block, "RTN", optional=True), "IN1": inverted, "IN2": delayed},
            {"DELAY": p.get("logic_delay", "2ps")},
            row=1,
            col=2,
            unconnected_pins=("OUT_BAR",),
        ),
    ]


def _comparator_latch(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = _parameters(block)
    set_net = _internal(str(block["id"]), "set")
    return [
        _leaf(
            block,
            "comparator",
            "digital_comparator_grounded",
            {"INP": _net(block, "REF"), "INN": _net(block, "SENSE"), "RTN": _net(block, "RTN", optional=True), "OUT": set_net},
            {"HYSTWD": p.get("hysteresis", "2mV"), "DELAY": p.get("comparator_delay", "2ps")},
            unconnected_pins=("OUT_BAR",),
        ),
        _leaf(
            block,
            "latch",
            "sr_latch_grounded",
            {"S": set_net, "R": _net(block, "RESET"), "Q": _net(block, "Q"), "QN": _net(block, "QN")},
            {
                "OUT_DELAY": p.get("latch_delay", "2ps"),
                "IC": p.get("initial_q", "0"),
                # The one-shot reset must win if set and reset overlap at a
                # switching boundary or during startup release.
                "DOM": p.get("dominance", "R"),
            },
            col=1,
        ),
    ]


def _behavioral_voltage_amplifier(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = _parameters(block)
    return [
        _leaf(
            block,
            "gain",
            "vcvs",
            {"P": _net(block, "OUT"), "N": _net(block, "RTN"), "CP": _net(block, "INP"), "CN": _net(block, "INN")},
            {"VALUE": p.get("gain", "1000")},
        )
    ]


def _behavioral_ota(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = _parameters(block)
    return [
        _leaf(
            block,
            "gm",
            "vccs",
            {"P": _net(block, "OUT"), "N": _net(block, "RTN"), "CP": _net(block, "INP"), "CN": _net(block, "INN")},
            {"VALUE": p.get("gm", "1mS")},
        )
    ]


def _deadtime_driver(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = _parameters(block)
    buffered = _internal(str(block["id"]), "buffered")
    inverted = _internal(str(block["id"]), "inverted")
    return [
        _leaf(
            block,
            "buffer",
            "digital_buffer_grounded",
            {"OUT": buffered, "OUT_BAR": inverted, "IN1": _net(block, "PWM")},
            {"DELAY": p.get("delay", "2ns")},
        ),
        _leaf(
            block,
            "hs_delay",
            "asymmetric_delay_grounded",
            {"OUT": _net(block, "PWM_HS"), "IN1": buffered},
            {"RISE_DELAY": p.get("deadtime_rise", "20ns"), "FALL_DELAY": "2ps"},
            col=1,
        ),
        _leaf(
            block,
            "ls_delay",
            "asymmetric_delay_grounded",
            {"OUT": _net(block, "PWM_LS"), "IN1": inverted},
            {"RISE_DELAY": p.get("deadtime_fall", "20ns"), "FALL_DELAY": "2ps"},
            row=1,
            col=1,
        ),
    ]


_EXPANDERS: dict[str, Callable[[Mapping[str, Any]], list[dict[str, Any]]]] = {
    "half_bridge": _half_bridge,
    "adaptive_ton": _adaptive_ton,
    "comparator_latch": _comparator_latch,
    "deadtime_driver": _deadtime_driver,
    "synthetic_ripple": _synthetic_ripple,
    "feedback": _feedback,
    "behavioral_voltage_amplifier": _behavioral_voltage_amplifier,
    "behavioral_ota": _behavioral_ota,
}


def expand_blocks(circuit: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an expanded circuit and block-to-leaf/network manifest."""

    value = deepcopy(dict(circuit))
    blocks = value.pop("blocks", [])
    if blocks is None:
        blocks = []
    if not isinstance(blocks, list):
        raise ValidationError("blocks must be a list")
    components = list(value.get("components", []))
    if not isinstance(components, list):
        raise ValidationError("components must be a list")
    manifest: dict[str, Any] = {}
    for index, raw in enumerate(blocks):
        block = _mapping(raw, f"blocks[{index}]")
        block_id = block.get("id")
        block_type = block.get("type")
        if not isinstance(block_id, str) or not block_id:
            raise ValidationError("Functional block needs a non-empty id", index=index)
        if block_type not in _EXPANDERS:
            raise ValidationError("Unsupported functional block type", block=block_id, block_type=block_type, supported=sorted(_EXPANDERS))
        leaves = _EXPANDERS[str(block_type)](block)
        components.extend(leaves)
        external = dict(block.get("ports", {}))
        external_values = {str(net) for net in external.values()}
        internal = sorted(
            {
                str(net)
                for leaf in leaves
                for net in leaf["pins"].values()
                if str(net) not in external_values and str(net).casefold() not in {"0", "gnd", "ground", "rtn"}
            }
        )
        manifest[block_id] = {
            "type": block_type,
            "leaf_components": [leaf["id"] for leaf in leaves],
            "external_networks": external,
            "internal_networks": internal,
            "parameters": dict(block.get("parameters", {})) if isinstance(block.get("parameters"), Mapping) else {},
            "layout": dict(block.get("layout", {})) if isinstance(block.get("layout"), Mapping) else {},
        }
    value["components"] = components
    # Rebuild nets from leaf pins.  The schema validator later checks this map.
    endpoints: dict[str, list[str]] = {}
    for component in components:
        if not isinstance(component, Mapping):
            continue
        for pin, net in dict(component.get("pins", {})).items():
            endpoints.setdefault(str(net), []).append(f"{component.get('id')}.{pin}")
    value["nets"] = {name: {"endpoints": items} for name, items in sorted(endpoints.items())}
    return value, manifest
