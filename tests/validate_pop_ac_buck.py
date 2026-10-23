import argparse
import csv
import json
from pathlib import Path
import shlex
import shutil
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from simplis_tools import _t2_header, run_deck, time_mean
from simplis_automation_v2.importer import _parse_instances_and_wires
from simplis_automation_v2.native_runner import run_native
from simplis_automation_v2.run_storage import protect, register_metrics
from simplis_automation_v2.runtime import resolve_runtime
from simplis_automation_v2.vectors import parse_show_file
from simplis_automation_v2.waveform_validation import validate_ac_response

SOURCE = ROOT / "examples/native/acot_buck"


def write_csv(path, names, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(names)
        writer.writerows(rows)


def native_netlist_check(path):
    sections = {"main": []}
    current = "main"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("*"):
            continue
        row = shlex.split(line)
        if row[0].lower() == ".subckt":
            current = row[1]
            sections[current] = []
        elif row[0].lower() == ".ends":
            current = "main"
        else:
            sections[current].append(row)
    core = {r[0]: r for r in sections["buck_core"] if not r[0].startswith(".")}
    nets = {r[1]: r[2] for r in sections["buck_core"] if r[0] == ".node_map"}
    assert core["V$IPRB_IN"][1:3] == [nets["SUPPLY"], nets["VIN"]]
    assert nets["SUPPLY"] != nets["VIN"]
    assert core["X$POWER_HS"][1:5] == [nets["VIN"], nets["SW"], nets["PWM_HS"], "0"]
    assert core["X$POWER_LS"][1:5] == ["0", nets["SW"], nets["PWM_LS"], "0"]
    assert core["!R$POWER_D_HS_BODY"][1:3] == [nets["SW"], nets["VIN"]]
    assert core["!R$POWER_D_LS_BODY"][1:3] == ["0", nets["SW"]]
    assert core["L1"][1] == nets["SW"]
    assert core["V$IPRB_L"][1:3] == [core["L1"][2], nets["VOUT"]]
    assert core["V$IPRB_C"][1:3] == [nets["VOUT"], core["COUT"][1]]
    assert core["RESR"][1:3] == [core["COUT"][2], "0"]
    assert core["XSENSE"][1:3] == [nets["VOUT"], nets["SW"]]
    assert core["XCTRL"][2] == core["XSENSE"][3]
    assert core["XCTRL"][3] == core["XTON"][2]
    assert core["XCTRL"][5] == core["XTON"][1] == nets["PWM_RAW"]
    assert core["XDRV"][1:4] == [nets["PWM_RAW"], nets["PWM_HS"], nets["PWM_LS"]]
    assert core["XPOP"][1:3] == [nets["PWM_HS"], nets["TRIG_GATE"]]
    sense = {r[0]: r for r in sections["sensing"] if not r[0].startswith(".")}
    sn = {r[1]: r[2] for r in sections["sensing"] if r[0] == ".node_map"}
    assert sense["V$FEEDBACK_AC"][1:5] == [sn["FB"], sn["FB_DIV"], "AC", "1"]
    assert sense["R$FEEDBACK_RT"][1:4] == [sn["VOUT"], sn["FB_DIV"], "90000"]
    assert sense["R$FEEDBACK_RB"][1:4] == [sn["FB_DIV"], "0", "10000"]
    assert sense["E$RIPPLE_E"][2:6] == [sn["RIP_BASE"], sn["SW"], sn["VOUT"], "0.1"]
    assert sense["C$RIPPLE_C"][1:3] == [sn["SENSE"], sn["RIP_BASE"]]
    return dict(power_stage=True, output_filter=True, feedback_loop=True, ac_injection=True,
                comparator_timer_driver=True, pop_trigger=True, checked_native_netlist=str(path))


def negative_source(destination):
    shutil.copytree(SOURCE, destination)
    path = destination / "buck_core.sxcmp"
    lines = path.read_text(encoding="utf-8").splitlines()
    instances, _, diagnostics = _parse_instances_and_wires("\n".join(lines))
    assert not diagnostics
    trigger = next(i for i in instances if i["properties"].get("REF") == "XPOP")
    for index in range(trigger["line"]-1, trigger["end_line"]):
        if lines[index].startswith('Property name="VREF" '):
            lines[index] = lines[index].replace('value="2.5"', 'value="50"')
    path.write_text("\n".join(lines)+"\n", encoding="utf-8")
    path = destination / "buck_pop_ac.sxsch"
    text = path.read_text(encoding="utf-8").replace("TD_RUN_AFTER_POP_FAILS=-1", "TD_RUN_AFTER_POP_FAILS=0")
    path.write_text(text.replace("POP_OUTPUT_CYCLES=5", "POP_OUTPUT_CYCLES=5 POP_SHOWDATA"), encoding="utf-8")


def validate(out, runtime):
    out.mkdir(parents=True, exist_ok=False)
    report = dict(runtime=runtime, source=str(SOURCE), tests={})
    (out / "data").mkdir()
    startup_vectors = ["#VOUT", "XBUCK.IPRB_L#P", "IPRB_LOAD#P", "XBUCK.#PWM_HS", "XBUCK.#PWM_LS", "XBUCK.#SW"]
    startup = run_native(SOURCE/"buck_startup.sxsch", out/"startup", runtime=runtime, vectors=startup_vectors)
    assert startup["completed"], startup
    data_root = out/"startup/execution/source/SIMPLIS_Data"
    folder = out/"startup/execution/vectors"
    records = [parse_show_file(folder/f"simplis_tran1_{name}.txt") for name in
               ("vout","xbuck_iprb_l_p","iprb_load_p","xbuck_pwm_hs","xbuck_pwm_ls","xbuck_sw")]
    assert all(r["x"]==records[0]["x"] for r in records)
    rows = list(zip(records[0]["x"], *(r["y"] for r in records)))
    indexes = list(range(7))
    write_csv(out/"data/startup.csv", ["time_s","vout_V","il_A","load_A","gh_V","gl_V","sw_V"], rows)
    voltage = indexes[1]
    steady_mean = time_mean(rows, voltage, 300e-6, 400e-6)
    assert abs(steady_mean - 1.2) / 1.2 < .01
    assert rows[0][voltage] < .06
    assert max(r[voltage] for r in rows if r[0] < 200e-6) < 1.26
    gate = indexes[4]
    edges = [right[0] for left, right in zip(rows, rows[1:]) if left[gate]<2.5<=right[gate] and right[0] > 300e-6]
    frequency = (len(edges)-1)/(edges[-1]-edges[0])
    assert 400e3 < frequency < 650e3
    assert all(not(r[indexes[4]]>2.5 and r[indexes[5]]>2.5) for r in rows)
    metrics = dict(vout_10A_mean_V=steady_mean, fsw_Hz=frequency, startup_peak_V=max(r[voltage] for r in rows if r[0]<200e-6))
    report["tests"]["startup"] = metrics
    register_metrics(out/"startup", metrics)
    report["topology"] = native_netlist_check(data_root/"buck_startup.net")

    ac_vectors = ["XBUCK.XSENSE.#FB", "XBUCK.XSENSE.#FB_DIV"]
    ac = run_native(SOURCE/"buck_pop_ac.sxsch", out/"ac", runtime=runtime, group="simplis_ac1", vectors=ac_vectors)
    assert ac["completed"], ac
    ac_data = out/"ac/execution/vectors"
    inject = parse_show_file(ac_data/"simplis_ac1_xbuck_xsense_fb.txt")
    returned = parse_show_file(ac_data/"simplis_ac1_xbuck_xsense_fb_div.txt")
    assert inject["x"] == returned["x"]
    vinj = np.array([complex(y["real"], y["imag"]) for y in inject["y"]])
    vret = np.array([complex(y["real"], y["imag"]) for y in returned["y"]])
    assert np.all(np.abs(vinj)>0)
    response = -vret/vinj
    validation = validate_ac_response(inject["x"], [dict(real=z.real, imag=z.imag) for z in response])
    assert validation["valid"], validation
    assert len(response)==186
    write_csv(out/"data/ac.csv", ["frequency_Hz","inject_real","inject_imag","return_real","return_imag","loop_real","loop_imag","magnitude_dB","phase_deg"],
              zip(inject["x"],vinj.real,vinj.imag,vret.real,vret.imag,response.real,response.imag,20*np.log10(abs(response)),np.unwrap(np.angle(response))*180/np.pi))
    report["tests"]["ac"] = validation
    report["tests"]["pop_log"] = ac["execution"]["solver_diagnostics"]["pop_records"]
    register_metrics(out/"ac", validation)

    pv = ["#VOUT", "XBUCK.#PWM_HS", "XBUCK.#PWM_LS", "XBUCK.#SW", "XBUCK.IPRB_L#P"]
    pop = run_native(SOURCE/"buck_pop_ac.sxsch", out/"pop", runtime=runtime, group="simplis_pop1", vectors=pv)
    assert pop["completed"], pop
    folder = out/"pop/execution/vectors"
    records = [parse_show_file(folder/f"simplis_pop1_{name}.txt") for name in ("vout","xbuck_pwm_hs","xbuck_pwm_ls","xbuck_sw","xbuck_iprb_l_p")]
    assert all(r["x"]==records[0]["x"] for r in records)
    samples = list(zip(records[0]["x"], *(r["y"] for r in records)))
    write_csv(out/"data/pop.csv", ["time_s","vout_V","gh_V","gl_V","sw_V","il_A"], samples)
    period = (samples[-1][0]-samples[0][0])/5
    pop_mean = time_mean(samples, 1, samples[0][0], samples[-1][0])
    i_mean = time_mean(samples, 5, samples[0][0], samples[-1][0])
    assert 4.9 < i_mean < 5.1
    assert abs(pop_mean-1.2)/1.2 < .01
    assert min(row[5] for row in samples)>0
    report["tests"]["pop"] = dict(samples=len(samples), cycles=5, period_s=period, frequency_Hz=1/period,
                                      vout_5A_mean_V=pop_mean, il_5A_mean_A=i_mean,
                                      vout_end_difference_V=samples[-1][1]-samples[0][1],
                                      il_end_difference_A=samples[-1][5]-samples[0][5])
    register_metrics(out/"pop", report["tests"]["pop"])

    negative_source(out/"negative-source")
    failure = run_native(out/"negative-source/buck_pop_ac.sxsch", out/"negative", runtime=runtime,
                         group="simplis_ac1", vectors=ac_vectors, timeout=60)
    assert not failure["completed"]
    messages = failure["execution"]["solver_diagnostics"]["messages"]
    assert any(m["error_id"]=="5020" and "triggering condition" in m["text"] for m in messages), messages
    report["tests"]["negative"] = dict(expected_failure=True, messages=messages)

    deck = out/"ac/execution/source/SIMPLIS_Data/buck_pop_ac.deck"
    standalone = run_deck(Path(runtime["simplis_exe"]), deck, out/"standalone", timeout=60)
    assert standalone["completed"], standalone
    report["tests"]["standalone"] = dict(completed=True, waveforms=standalone["waveforms"])
    # 全部数据来自本轮全新目录；GUI 和独立求解器的复数响应逐频点比较
    with (out/"standalone/input.ckt.f2").open(encoding="ascii") as stream:
        _, header = _t2_header(stream, axis="FREQUENCY")
        table = np.loadtxt(stream)
    # 使用文件中的列名定位，避免依赖探针排列顺序
    real = header.index("V(XBUCK.XSENSE.#FB_DIV).REAL")
    imag = header.index("V(XBUCK.XSENSE.#FB_DIV).IMAG")
    direct_return = table[:,real] + 1j*table[:,imag]
    difference = float(np.max(np.abs(direct_return-vret)/np.maximum(1e-12,np.abs(vret))))
    assert difference < 2e-5, difference
    report["tests"]["standalone"]["max_relative_return_difference"] = difference
    for name in ("startup","ac","pop","standalone"):
        protect(out/name, "Final POP/AC Buck validation evidence")
    (out/"validation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report["tests"],ensure_ascii=False,indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Opt-in real native Buck startup/POP/AC and failure diagnostics")
    parser.add_argument("--out-dir",type=Path,required=True)
    parser.add_argument("--install",type=Path)
    args = parser.parse_args()
    validate(args.out_dir.resolve(),resolve_runtime(install=args.install))
