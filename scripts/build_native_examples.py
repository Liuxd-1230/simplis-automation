from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from simplis_tools import symbols

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from simplis_automation_v2.compiler import _transform_pin


class Sheet:
    # 原生对象使用实际符号定义，布局和拓扑由调用处指定
    def __init__(self):
        self.definitions = {}
        self.items = []
        self.pins = {}

    def instance(self, name, ref, x, y, orient="N0", **values):
        definition = symbols(ROOT / "assets/device-reference/devices.sxsch", name=name)["symbols"][0]
        self.definitions[name] = definition["definition"]
        properties = {p["name"]: p.get("value", "") for p in definition["properties"]}
        for key, value in values.items():
            actual = next((name for name in properties if name.casefold() == key.casefold()), key)
            properties[actual] = value
        if ref:
            actual = next((name for name in properties if name.casefold() == "ref"), "REF")
            properties[actual] = ref
        self.items.append(f'.Instance\nAttributes type=symbol name="{name}" x={x} y={y} orient={orient}')
        for key, value in properties.items():
            visible = int(key in {"REF", "VALUE"})
            position = 'autopos=1 normal=Right rotated=Top'
            original = next((p for p in definition["properties"] if p["name"] == key), {})
            if key == "REF" and original.get("normal") == "Top":
                position = 'autopos=1 normal=Top rotated=Top'
            if name == "vcvs" and visible:
                position = f'autopos=0 x=120 y={-240 if key == "REF" else -120} align=LeftBase'
            if name == "modport" and key == "VALUE":
                position = 'autopos=0 x=360 y=-120 align=LeftBase'
            self.items.append(f'Property name="{key}" value="{value}" {position} visible={visible} font=Default order=-1')
        self.items.append("Netnames\n.EndInstance")
        for pin in definition["pins"]:
            dx, dy = _transform_pin(int(pin["x"]), int(pin["y"]), orient)
            self.pins[(ref, pin["name"])] = (x + dx, y + dy)

    def wire(self, *points):
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            if x1 != x2 and y1 != y2:
                raise ValueError("Use orthogonal routes")
            if (x1, y1) != (x2, y2):
                self.items.extend([f'Wire x1={x1} y1={y1} x2={x2} y2={y2}', '.Wire',
                                   f'Attributes x1={x1} y1={y1} x2={x2} y2={y2}', '.EndWire'])

    def port(self, name, x, y, orient="N0"):
        self.instance("modport", None, x, y, orient, VALUE=name, scterm="1")

    def ground(self, x, y):
        self.instance("gnd", None, x, y)

    def label(self, name, x, y, orient="N0"):
        self.instance("term", None, x, y, orient, VALUE=name)

    def module(self, filename, ref, name, x):
        self.items.extend([f'.Instance\nAttributes type=component path="{filename}" x={x} y=0 orient=N0',
                           f'Property name="REF" value="{ref}" autopos=0 x=120 y=-240 align=LeftBase font=Default order=-1',
                           f'Property name="ModuleName" value="{name}" autopos=0 x=120 y=-60 align=LeftBase font=Default order=-1',
                           'Property name="MODEL" value="X" visible=0',
                           'Property name="SIMPLIS_TEMPLATE" value="<ref> <nodelist> <value>" visible=0',
                           'Property name="SIMULATOR" value="Simplis" visible=0',
                           'Netnames\n.EndInstance'])

    def save(self, path, interface="", analysis=""):
        text = ("SIMetrixFile type=schematic format=1.0 revision=8\n.Component\n" + interface +
                ".Schematic\n.SymbolLibrary\n" + "\n".join(self.definitions.values()) +
                "\n.EndSymbolLibrary\n" + "\n".join(self.items) +
                f'\nText value="{analysis}"\nSimulatorMode value="Simplis"\n.EndSchematic\n.EndComponent\n')
        path.write_text("\n".join(line.rstrip() for line in text.splitlines()) + "\n",
                        encoding="utf-8", newline="\n")


def interface(name, pins):
    result = ['.Symbol', f'Attributes format=1.0 revision=8 name="component" description="{name}" track=1',
              'Segment x1=0 y1=0 x2=1680 y2=0', 'Segment x1=1680 y1=0 x2=1680 y2=1440',
              'Segment x1=1680 y1=1440 x2=0 y2=1440', 'Segment x1=0 y1=1440 x2=0 y2=0']
    for order, (pin, x, y) in enumerate(pins, 1):
        offset, align = (120, "LeftBase") if x == 0 else (-120, "RightBase")
        result.append(f'Pin name="{pin}" order={order} x={x} y={y} visible=1 xlabel={offset} ylabel=-60 align={align} rotated=0')
    result.extend(['Property name="MODEL" value="X" visible=0',
                   f'Property name="ModuleName" value="{name}" visible=0',
                   'Property name="SIMULATOR" value="Simplis" visible=0', '.EndSymbol'])
    return "\n".join(result) + "\n"


def build(out: Path):
    if out.exists() and any(out.iterdir()):
        raise ValueError("Example directory must be new or empty")
    out.mkdir(parents=True, exist_ok=True)
    rc = Sheet()
    rc.instance("dc_source", "V1", 0, 0, VALUE="1")
    rc.instance("res", "R1", 720, 0, "N270", VALUE="1k")
    rc.instance("cap", "C1", 1440, 0, VALUE="1u")
    rc.wire(rc.pins["V1", "P"], rc.pins["R1", "P"])
    rc.wire(rc.pins["R1", "N"], rc.pins["C1", "P"])
    rc.wire(rc.pins["V1", "N"], (0, 720), (1440, 720), rc.pins["C1", "N"])
    rc.ground(0, 720)
    rc.wire((1440, 0), (2160, 0))
    rc.label("VOUT", 2160, 0)
    rc.save(out / "simple_rc.sxsch", analysis=r".OPTIONS PSP_NPT=1001\n.INIT V(C1)=0\n.TRAN 5m 0\n.PRINT V(#VOUT)")

    control = Sheet()
    control.instance("vcvs", "E1", 1200, 240, VALUE="9")
    control.port("VREF", 0, 360)
    control.port("VOUT", 0, 1080)
    control.port("DRIVE", 2880, 360, "N180")
    control.port("RTN", 2880, 1080, "N180")
    control.wire((0, 360), control.pins["E1", "CP"])
    control.wire((0, 1080), (840, 1080), (840, 600), control.pins["E1", "CN"])
    control.wire((2880, 360), control.pins["E1", "P"])
    control.wire((2880, 1080), (1800, 1080), (1800, 600), control.pins["E1", "N"])
    control.save(out / "controller.sxcmp", interface("Controller", [
        ("VREF", 0, 360), ("VOUT", 0, 1080), ("DRIVE", 1680, 360), ("RTN", 1680, 1080)]))

    plant = Sheet()
    plant.instance("res", "R1", 720, 360, "N270", VALUE="1k")
    plant.instance("cap", "C1", 1920, 360, VALUE="1u")
    plant.port("DRIVE", 0, 360)
    plant.port("VOUT", 2880, 360, "N180")
    plant.port("RTN", 2880, 1080, "N180")
    plant.wire((0, 360), plant.pins["R1", "P"])
    plant.wire(plant.pins["R1", "N"], plant.pins["C1", "P"], (2880, 360))
    plant.wire(plant.pins["C1", "N"], (1920, 1080), (2880, 1080))
    plant.save(out / "plant.sxcmp", interface("RC plant", [
        ("DRIVE", 0, 360), ("VOUT", 1680, 360), ("RTN", 1680, 1080)]))

    top = Sheet()
    top.instance("dc_source", "V1", 600, 360, VALUE="1")
    top.module("controller.sxcmp", "XCTRL", "Controller", 1920)
    top.module("plant.sxcmp", "XPLANT", "RC plant", 5280)
    top.wire((600, 360), (1920, 360))
    top.wire((3600, 360), (5280, 360))
    top.wire((6960, 360), (7560, 360), (7560, 2040), (1440, 2040), (1440, 1080), (1920, 1080))
    top.wire((600, 840), (600, 1080))
    top.ground(600, 1080)
    for x in (3600, 6960):
        top.wire((x, 1080), (x, 1320))
        top.ground(x, 1320)
    top.label("VOUT", 7560, 360)
    top.save(out / "feedback.sxsch", analysis=r".OPTIONS PSP_NPT=1001\n.INIT V(XPLANT.C1)=0\n.TRAN 1m 0\n.PRINT V(#VOUT)")
    return dict(directory=str(out), files=[p.name for p in out.iterdir()],
                theory=dict(rc_tau_s=.001, feedback_dc_V=.9, feedback_tau_s=.0001))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the two fixed editable native examples; requires optional v2 dependencies")
    parser.add_argument("--out-dir", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().out_dir), indent=2))
