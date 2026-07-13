"""The intentionally small public command line for simplis-automation v2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .catalog import approve_catalog_candidate, catalog_runtime_evidence, create_catalog_candidate, load_catalog
from .compiler import compile_circuit
from .errors import V2Error


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _overrides(values: list[str] | None) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            raise V2Error("Parameter override must use NAME=VALUE", "invalid_override", {"value": item})
        name, value = item.split("=", 1)
        if not name:
            raise V2Error("Parameter override name cannot be empty", "invalid_override", {"value": item})
        output[name] = value
    return output


def _runtime_from_args(args: argparse.Namespace) -> dict[str, Any]:
    from .runtime import resolve_runtime

    return resolve_runtime(
        simetrix_exe=getattr(args, "simetrix_exe", None),
        symbol_library_dir=getattr(args, "symbol_lib_dir", None),
        config_path=getattr(args, "runtime_config", None),
    )


def command_doctor(args: argparse.Namespace) -> dict[str, Any]:
    runtime = _runtime_from_args(args)
    result: dict[str, Any] = {"runtime": runtime, "ready": bool(runtime.get("ready"))}
    if args.catalog:
        catalog = load_catalog(args.catalog)
        evidence = catalog_runtime_evidence(catalog, runtime.get("symbol_library_dir"))
        result["catalog"] = evidence
        result["ready"] = bool(result["ready"] and evidence.get("ready"))
    return result


def command_catalog_import(args: argparse.Namespace) -> dict[str, Any]:
    candidate = create_catalog_candidate(args.symbol_lib_dir, args.symbol, args.kind, args.out, role=args.role)
    return {"ok": True, "candidate": str(Path(args.out).resolve()), "device": candidate["device"]}


def command_catalog_approve(args: argparse.Namespace) -> dict[str, Any]:
    catalog = approve_catalog_candidate(args.candidate, args.out, base_catalog_path=args.base_catalog)
    return {"ok": True, "catalog": str(Path(args.out).resolve()), "fingerprint": catalog["fingerprint"]}


def command_compile(args: argparse.Namespace) -> dict[str, Any]:
    manifest = compile_circuit(args.circuit, args.out_dir, _overrides(args.set), args.catalog)
    return {"ok": True, "manifest": str(Path(args.out_dir).resolve() / "build-manifest.json"), "result": manifest}


def command_verify(args: argparse.Namespace) -> dict[str, Any]:
    from .verifier import verify_manifest

    result = verify_manifest(args.manifest, runtime=_runtime_from_args(args), timeout=args.timeout, interactive=args.interactive)
    return {"ok": bool(result.get("ok")), "result": result}


def command_sweep(args: argparse.Namespace) -> dict[str, Any]:
    from .sweep import run_sweep

    result = run_sweep(
        args.experiment,
        args.out_dir,
        runtime=_runtime_from_args(args),
        max_cases=args.max_cases,
        allow_large=args.allow_large,
    )
    return {"ok": bool(result.get("ok", True)), "result": result}


def command_import_schematic(args: argparse.Namespace) -> dict[str, Any]:
    from .importer import import_schematic

    result = import_schematic(args.input, load_catalog(args.catalog), args.out)
    # A draft with pending catalog candidates is a successful import operation,
    # but is explicitly not a verifiable circuit until those candidates are
    # approved.  Keep CLI process success separate from circuit validity.
    status = result.get("metadata", {}).get("import_status") if isinstance(result, dict) else None
    return {"ok": True, "verifiable": status not in {"catalog_pending", "blocked"}, "result": result}


def command_parameter_promote(args: argparse.Namespace) -> dict[str, Any]:
    from .importer import promote_parameter

    result = promote_parameter(
        args.circuit,
        args.component,
        args.property,
        args.name,
        args.dimension,
        args.minimum,
        args.maximum,
        args.out,
    )
    return {"ok": True, "result": result}


def _runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--simetrix-exe", help="Explicit SIMetrix.exe path")
    parser.add_argument("--symbol-lib-dir", help="Explicit installed .sxslb directory")
    parser.add_argument("--runtime-config", help="Optional v2 runtime config YAML/JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="simplis-v2", description="Evidence-first SIMetrix/SIMPLIS circuit compiler")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Validate runtime and an optional locked catalog")
    _runtime_options(doctor)
    doctor.add_argument("--catalog")
    doctor.set_defaults(handler=command_doctor)

    catalog = sub.add_parser("catalog", help="Create and approve local device catalog entries")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)
    item = catalog_sub.add_parser("import", help="Extract a pending device candidate from installed symbol libraries")
    item.add_argument("symbol")
    item.add_argument("--kind", required=True)
    item.add_argument("--role")
    item.add_argument("--symbol-lib-dir", required=True)
    item.add_argument("--out", required=True)
    item.set_defaults(handler=command_catalog_import)
    approve = catalog_sub.add_parser("approve", help="Merge a reviewed candidate into a catalog")
    approve.add_argument("candidate")
    approve.add_argument("--out", required=True)
    approve.add_argument("--base-catalog")
    approve.set_defaults(handler=command_catalog_approve)

    compile_parser = sub.add_parser("compile", help="Compile YAML into a static-valid script and manifest")
    compile_parser.add_argument("circuit")
    compile_parser.add_argument("--out-dir", required=True)
    compile_parser.add_argument("--catalog")
    compile_parser.add_argument("--set", action="append", help="Parameter override NAME=VALUE; repeat as needed")
    compile_parser.set_defaults(handler=command_compile)

    verify = sub.add_parser("verify", help="Create a schematic and actual SIMPLIS netlist from a manifest")
    verify.add_argument("manifest")
    _runtime_options(verify)
    verify.add_argument("--timeout", type=float, default=120.0)
    verify.add_argument("--interactive", action="store_true")
    verify.set_defaults(handler=command_verify)

    sweep = sub.add_parser("sweep", help="Run a deterministic compile plus netlist-verify grid")
    sweep.add_argument("experiment")
    sweep.add_argument("--out-dir", required=True)
    _runtime_options(sweep)
    sweep.add_argument("--max-cases", type=int, default=128)
    sweep.add_argument("--allow-large", action="store_true")
    sweep.set_defaults(handler=command_sweep)

    importer = sub.add_parser("import-schematic", help="Import a readable text .sxsch as v2 YAML")
    importer.add_argument("input")
    importer.add_argument("--catalog", required=True)
    importer.add_argument("--out", required=True)
    importer.set_defaults(handler=command_import_schematic)

    parameter = sub.add_parser("parameter", help="Manage declared YAML parameter cards")
    parameter_sub = parameter.add_subparsers(dest="parameter_command", required=True)
    promote = parameter_sub.add_parser("promote", help="Promote one imported component property to a parameter")
    promote.add_argument("circuit")
    promote.add_argument("component")
    promote.add_argument("property")
    promote.add_argument("--name", required=True)
    promote.add_argument("--dimension", required=True)
    promote.add_argument("--min", dest="minimum", required=True)
    promote.add_argument("--max", dest="maximum", required=True)
    promote.add_argument("--out", required=True)
    promote.set_defaults(handler=command_parameter_promote)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.handler(args)
    except V2Error as exc:
        _emit(exc.as_dict())
        return exc.exit_code
    except Exception as exc:  # pragma: no cover - last-resort CLI containment
        _emit({"ok": False, "error": {"code": "internal_error", "message": str(exc), "details": {}}})
        return 1
    _emit(payload)
    return 0 if payload.get("ok", True) else 4


if __name__ == "__main__":
    raise SystemExit(main())
