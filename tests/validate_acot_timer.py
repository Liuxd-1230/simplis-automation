import argparse
import json
from pathlib import Path
import shutil
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from simplis_automation_v2.importer import _parse_instances_and_wires
from simplis_automation_v2.native_runner import run_native
from simplis_automation_v2.runtime import resolve_runtime
from simplis_automation_v2.run_storage import protect, register_metrics
from simplis_automation_v2.vectors import parse_show_file
from simplis_automation_v2.waveform_validation import validate_ac_response
from simplis_tools import time_mean
from validate_pop_ac_buck import native_netlist_check, write_csv

SOURCE = ROOT / 'examples/native/acot_buck'


def set_input(path, voltage):
    lines = path.read_text(encoding='utf-8').splitlines()
    instances, _, diagnostics = _parse_instances_and_wires('\n'.join(lines))
    assert not diagnostics
    source = next(i for i in instances if i['properties'].get('REF') == 'VIN1')
    original = 'value="' + source['properties']['VALUE'] + '"'
    for index in range(source['line']-1, source['end_line']):
        if lines[index].startswith('Property name="VALUE" '):
            lines[index] = lines[index].replace(original, f'value="{voltage}"')
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')


def load(run, group, slug):
    return parse_show_file(run / 'execution/vectors' / f'{group}_{slug}.txt')


def pulses(record, start, end, level=.5):
    result = []
    rising = None
    for left, right in zip(zip(record['x'], record['y']), zip(record['x'][1:], record['y'][1:])):
        if left[1] < level <= right[1]:
            rising = right[0]
        if left[1] >= level > right[1] and rising is not None:
            if start <= rising and right[0] <= end:
                result.append((rising, right[0]))
            rising = None
    assert len(result) >= 3, result
    return np.array(result)


def wave_csv(path, records):
    # 模拟量保留事件时间；数字量按前值保持，避免在两个开关边沿之间插入斜坡。
    axis = np.array(records['vout_V']['x'])
    channels = []
    for name, record in records.items():
        if name in {'pwm_logic', 'gh_V', 'gl_V'}:
            indices = np.searchsorted(record['x'], axis, side='right') - 1
            assert np.min(indices) >= 0
            channels.append(np.array(record['y'])[indices])
        else:
            channels.append(np.interp(axis, record['x'], record['y']))
    write_csv(path, ['time_s', *records], zip(axis, *channels))


def validate(out, runtime):
    out.mkdir(parents=True, exist_ok=False)
    (out / 'data').mkdir()
    report = dict(runtime=runtime, steady={}, line_step={})
    for voltage in (5, 12, 18):
        bench = out / f'source_{voltage}V'
        shutil.copytree(SOURCE, bench)
        set_input(bench / 'buck_pop_ac.sxsch', voltage)
        run = out / f'pop_{voltage}V'
        vectors = ['#VOUT', 'XBUCK.#PWM_RAW', 'XBUCK.#PWM_HS', 'XBUCK.#PWM_LS',
                   'XBUCK.XTON.#RAMP', 'XBUCK.XTON.#VTH', 'XBUCK.IPRB_L#P']
        result = run_native(bench/'buck_pop_ac.sxsch', run, runtime=runtime,
                            group='simplis_pop1', vectors=vectors)
        assert result['completed'], result
        records = {name: load(run, 'simplis_pop1', slug) for name, slug in [
            ('vout_V', 'vout'), ('pwm_logic', 'xbuck_pwm_raw'), ('gh_V', 'xbuck_pwm_hs'),
            ('gl_V', 'xbuck_pwm_ls'), ('ramp_V', 'xbuck_xton_ramp'),
            ('vth_V', 'xbuck_xton_vth'), ('il_A', 'xbuck_iprb_l_p')]}
        vout = records['vout_V']
        start, end = vout['x'][0], vout['x'][-1]
        mean = time_mean(list(zip(vout['x'], vout['y'])), 1, start, end)
        assert abs(mean-1.2)/1.2 < .01, mean
        edges = pulses(records['gh_V'], start, end, 2.5)
        widths = edges[:, 1]-edges[:, 0]
        ideal = 10e-12 * mean / (5e-6 * voltage)
        measured = float(np.mean(widths))
        assert 0 < measured-ideal < 12e-9, (voltage, measured, ideal)
        ramp = records['ramp_V']
        reset_values = np.interp(edges[:, 0], ramp['x'], ramp['y'])
        assert np.max(np.abs(reset_values)) < .002, reset_values
        lo = edges[:, 0] + .25*widths
        hi = edges[:, 0] + .75*widths
        slopes = (np.interp(hi, ramp['x'], ramp['y'])-np.interp(lo, ramp['x'], ramp['y']))/(hi-lo)
        assert np.max(np.abs(slopes/(5e-6*voltage/10e-12)-1)) < .001, slopes
        assert records['gh_V']['x'] == records['gl_V']['x']
        assert not np.any((np.array(records['gh_V']['y']) > 2.5) & (np.array(records['gl_V']['y']) > 2.5))
        frequency = float((len(edges)-1)/(edges[-1, 0]-edges[0, 0]))
        assert 400e3 < frequency < 650e3, frequency
        wave_csv(out/f'data/pop_{voltage}V.csv', records)
        metrics = dict(vout_mean_V=mean, ton_ideal_s=ideal, ton_measured_s=measured,
                       excess_delay_s=measured-ideal, fsw_Hz=frequency,
                       reset_max_abs_V=float(np.max(np.abs(reset_values))),
                       ramp_slope_V_per_s=float(np.mean(slopes)),
                       topology=native_netlist_check(run/'execution/source/SIMPLIS_Data/buck_pop_ac.net'))
        ac_run = out / f'ac_{voltage}V'
        result = run_native(bench/'buck_pop_ac.sxsch', ac_run, runtime=runtime,
                            group='simplis_ac1', vectors=['XBUCK.XSENSE.#FB', 'XBUCK.XSENSE.#FB_DIV'])
        assert result['completed'], result
        inject = load(ac_run, 'simplis_ac1', 'xbuck_xsense_fb')
        returned = load(ac_run, 'simplis_ac1', 'xbuck_xsense_fb_div')
        assert inject['x'] == returned['x']
        response = -np.array([complex(**y) for y in returned['y']])/np.array([complex(**y) for y in inject['y']])
        metrics['ac'] = validate_ac_response(inject['x'], [dict(real=z.real, imag=z.imag) for z in response])
        assert metrics['ac']['valid'], metrics['ac']
        assert metrics['ac']['metrics']['phase_margin_deg'] > 45, metrics['ac']
        assert metrics['ac']['metrics']['gain_margin_db'] > 6, metrics['ac']
        write_csv(out/f'data/ac_{voltage}V.csv', ['frequency_Hz', 'loop_real', 'loop_imag', 'magnitude_dB', 'phase_deg'],
                  zip(inject['x'], response.real, response.imag, 20*np.log10(abs(response)), np.unwrap(np.angle(response))*180/np.pi))
        register_metrics(run, metrics)
        register_metrics(ac_run, metrics['ac'])
        protect(run, 'Final device-connected adaptive on-time timer verification')
        protect(ac_run, 'Final adaptive on-time AC evidence')
        report['steady'][str(voltage)] = metrics
        print(f'{voltage} V: ton={measured*1e9:.3f} ns, fsw={frequency/1e3:.3f} kHz', flush=True)

    run = out / 'line_step'
    result = run_native(SOURCE/'buck_line_step.sxsch', run, runtime=runtime, vectors=vectors)
    assert result['completed'], result
    records = {name: load(run, 'simplis_tran1', slug) for name, slug in [
        ('vout_V', 'vout'), ('pwm_logic', 'xbuck_pwm_raw'), ('gh_V', 'xbuck_pwm_hs'),
        ('gl_V', 'xbuck_pwm_ls'), ('ramp_V', 'xbuck_xton_ramp'),
        ('vth_V', 'xbuck_xton_vth'), ('il_A', 'xbuck_iprb_l_p')]}
    for label, lo, hi in [('5V_before', 170e-6, 195e-6), ('18V', 350e-6, 395e-6), ('5V_after', 550e-6, 595e-6)]:
        edges = pulses(records['gh_V'], lo, hi, 2.5)
        mean = time_mean(list(zip(records['vout_V']['x'], records['vout_V']['y'])), 1, lo, hi)
        assert abs(mean-1.2)/1.2 < .01, (label, mean)
        report['line_step'][label] = dict(vout_mean_V=mean, ton_mean_s=float(np.mean(edges[:, 1]-edges[:, 0])))
    assert report['line_step']['5V_before']['ton_mean_s'] > 3*report['line_step']['18V']['ton_mean_s']
    assert abs(report['line_step']['5V_after']['ton_mean_s']/report['line_step']['5V_before']['ton_mean_s']-1) < .02
    wave_csv(out/'data/line_step.csv', records)
    register_metrics(run, report['line_step'])
    protect(run, 'Final 5 to 18 to 5 V line-step evidence')
    (out/'validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Opt-in REAL native ACOT timer: 5/12/18 V POP/AC and line step')
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--install', type=Path)
    args = parser.parse_args()
    validate(args.out_dir.resolve(), resolve_runtime(install=args.install))
