import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from simplis_automation_v2.experiment_runner import run_experiment
from simplis_automation_v2.native_runner import run_native
from simplis_automation_v2.runtime import resolve_runtime
from simplis_automation_v2.run_storage import protect
from simplis_automation_v2.vectors import parse_show_file, summarize_show_file
from simplis_automation_v2.watchdog import WatchdogConfig, run_script_with_watchdog


def profiled(function):
    counts = Counter()
    def observe(frame, event, arg):
        if event == 'call':
            counts[frame.f_code.co_name] += 1
            if frame.f_code.co_name == 'run' and frame.f_globals.get('__name__') == 'subprocess':
                command = frame.f_locals['popenargs'][0]
                counts['process:' + Path(command[0]).name.casefold()] += 1
    sys.setprofile(observe)
    try:
        result = function()
    finally:
        sys.setprofile(None)
    names = ('_read_show_file', 'parse_show_file', 'summarize_show_file',
             '_powershell_json', 'simetrix_processes', 'process_snapshot',
             'capture_dialogs', 'query_simulator_status', '_artifact_snapshot',
             'process:powershell', 'process:sxcommand.exe')
    return result, {key: counts[key] for key in names}


def peak_bytes(function, source):
    tracemalloc.start()
    result = function(source)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, peak


def validate_script_error(work, runtime):
    work.mkdir(parents=True, exist_ok=False)
    status, log = work/'status.txt', work/'messages.log'
    script = work/'invalid.sxscr'
    script.write_text(f'''RedirectMessages dup "{log}"
Let ff=OpenEchoFile('{status}', 'w')
Echo script_started=true
Let ff=CloseEchoFile()
Let invalid = missing_vector_for_error_detection
''', encoding='utf-8')
    result = run_script_with_watchdog(simetrix_exe=Path(runtime['executable']), script=script,
                status_file=status, work_dir=work, job_id='real-script-error', message_log=log,
                config=WatchdogConfig(hard_timeout=20), assess_completion=False)
    assert not result['ok'] and result['classification'] == 'script_error_blocked', result
    return dict(classification=result['classification'], bundle=result['error_bundle'],
                termination_classification=result.get('termination_classification'))


def validate(out, runtime):
    out.mkdir(parents=True, exist_ok=False)
    report = {}
    result, counts = profiled(lambda: run_native(ROOT/'examples/native/simple_rc.sxsch', out/'native',
                                                runtime=runtime, vectors=['#VOUT']))
    assert result['completed'], result
    assert counts['parse_show_file'] == 0 and counts['summarize_show_file'] == 1, counts
    assert counts['process_snapshot'] == 0, counts
    source = out/'native/execution/vectors/simplis_tran1_vout.txt'
    full, full_peak = peak_bytes(parse_show_file, source)
    summary, summary_peak = peak_bytes(summarize_show_file, source)
    assert abs(full['y'][-1] - .993262053) < 1e-6
    assert full['sha256'] == summary['sha256'] and summary['samples'] == len(full['x'])
    assert summary_peak < full_peak/2, (summary_peak, full_peak)
    report['native'] = dict(counts=counts, samples=summary['samples'],
                            full_parse_peak_bytes=full_peak, summary_peak_bytes=summary_peak)
    protect(out/'native', 'Real native streaming waveform validation')
    (out/'validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    result, counts = profiled(lambda: run_experiment(ROOT/'examples/hysteretic_control_experiment.yaml',
                                                    out/'v2', runtime=runtime))
    assert result['ok'], result.get('errors')
    manifest = result['vector_manifest']
    assert counts['_read_show_file'] == len({v['path'] for v in manifest['vectors']}), counts
    assert counts['process_snapshot'] == 0, counts
    report['v2'] = dict(counts=counts, vectors=len(manifest['vectors']),
                       classification=manifest['classification'])
    protect(out/'v2', 'Real YAML control and one-read-per-vector validation')
    (out/'validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    report['script_error'] = validate_script_error(out/'script-error', runtime)
    (out/'validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Opt-in real native/YAML execution and streaming-memory checks')
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    validate(args.out_dir.resolve(), resolve_runtime())
