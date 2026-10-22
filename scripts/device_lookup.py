from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from simplis_tools import symbols


REFERENCE = Path(__file__).resolve().parents[1] / "assets/device-reference/index.json"
OFFICIAL_TERMS = {"二极管": ("diode",), "场效应管": ("mosfet",), "功率管": ("mosfet",),
                  "变压器": ("transformer",), "环路注入": ("bode", "injection")}


def installation(value: Path | None = None) -> Path:
    configured = value or os.environ.get("SIMPLIS_INSTALL") or shutil.which("simplis.exe")
    if not configured:
        raise ValueError("Provide --install <installation root or executable>, or set SIMPLIS_INSTALL")
    path = Path(configured).expanduser().resolve()
    if path.is_file():
        path = path.parent.parent
    if not (path / "support/symbollibs").is_dir():
        raise ValueError(f"No support/symbollibs below {path}")
    return path


def locate(value: Path | None = None, query: str = "") -> dict:
    root = installation(value)
    support = root / "support"
    terms = query.casefold().split()
    matches = [str(p) for folder in (support / "docs", support / "examples", support / "html")
               if folder.exists() for p in folder.rglob("*") if p.is_file()
               and (not terms or any(t in p.name.casefold() for t in terms))]
    return dict(installation=str(root), symbol_libraries=str(support / "symbollibs"),
                models=str(support / "models"), documents_and_examples=matches[:60],
                match_count=len(matches), evidence="installed file paths; search document contents with existing PDF/text tools",
                manual_topics={"ScriptReference.pdf": ["Inst", "Wire", "Prop", "Netlist", "PreProcessNetlist", "CopyClipSchem"],
                               "SIMPLIS_Reference.pdf": ["Voltage-Controlled Switch", "Transient Analysis", "PRINT", "Digital Devices"],
                               "UsersManual.pdf": ["Current Probes", "Hierarchical Schematic Entry", "Symbol Editor"]})


def find(query: str, install: Path | None = None, reference: Path = REFERENCE,
         official: bool = False) -> dict:
    if not query.strip():
        raise ValueError("Describe the required device function")
    index = json.loads(reference.read_text(encoding="utf-8"))
    terms = query.casefold().split()
    ranked = []
    for device in index["devices"]:
        phrases = [device["name"], device["chinese_name"], device["description"], *device["aliases"]]
        text = " ".join(phrases).casefold()
        score = sum(len(t) for t in terms if t in text)
        # 中文自然描述可包含“需要”等词，优先识别完整功能词组
        score += sum(len(p) for p in phrases if len(p) >= 2 and p.casefold() in query.casefold())
        if score:
            ranked.append((score, device))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    if ranked and not official:
        results = []
        for score, device in ranked[:5]:
            definition = symbols(reference.parent / index["schematic"], name=device["name"])["symbols"][0]
            results.append(dict(device, score=score, properties=definition["properties"],
                                definition=definition["definition"]))
        return dict(source="user-reference", reference=str(reference.resolve()), matches=results,
                    match_count=len(ranked),
                    next_step="Check suitability, instance values and model requirements; use --official with --install if unsuitable")
    root = installation(install)
    # 官方库仍使用已存在的符号解析器。参考索引只提供功能词与符号名称之间的映射
    translated = {d["name"].casefold() for _, d in ranked} | set(terms)
    translated.update(t for phrase, translations in OFFICIAL_TERMS.items() if phrase in query
                      for t in translations)
    found = []
    for library in sorted((root / "support/symbollibs").glob("*.sxslb")):
        for entry in symbols(library)["symbols"]:
            text = (entry["name"] + " " + entry["description"]).casefold()
            if any(t in text for t in translated):
                detail = symbols(library, name=entry["name"])["symbols"][0]
                found.append(dict(detail, library=str(library), evidence="installed symbol definition; electrical behavior not tested"))
    return dict(source="installed-official-libraries", matches=found[:20], match_count=len(found),
                help=locate(root, query),
                next_step="Inspect SIMPLIS_TEMPLATE, model scope and matching-version manual; no match means refine search, never invent a substitute")
