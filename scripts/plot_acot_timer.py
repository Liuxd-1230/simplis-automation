import argparse
from pathlib import Path
import sys

import numpy as np

parser = argparse.ArgumentParser(description='Plot real native ACOT timer validation data')
parser.add_argument('--results', type=Path, required=True)
parser.add_argument('--eeplot-assets', type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.eeplot_assets.resolve()))
import eeplot

root = args.results.resolve()
out = root/'figures'
out.mkdir(exist_ok=True)
eeplot.use_style('paper', width='single')

print('[pe-plots] type: A2; gallery: KIND=waveform-stack -> dab_exp_load_step')
d = eeplot.load_series(root/'data/pop_12V.csv')
print(f'[pe-plots] loaded pop_12V.csv: N={len(d["time_s"])} decimated=no')
t = (d['time_s']-d['time_s'][0])*1e6
fig, axes = eeplot.waveform_axes(2)
axes[0].step(t, d['gh_V'], where='post', color=eeplot.SYSTEM)
eeplot.channel_label(axes[0], r'$v_{GH}$ [V]')
eeplot.clean_ylim(axes[0], data=d['gh_V'], zero_base=True)
axes[1].plot(t, d['ramp_V'], color=eeplot.SYSTEM, label=r'$v_{ramp}$')
axes[1].plot(t, d['vth_V'], color=eeplot.GRAY, ls='--', label=r'$v_{th}$')
eeplot.channel_label(axes[1], r'$v_{ramp},v_{th}$ [V]')
eeplot.clean_ylim(axes[1], data=[d['ramp_V'], d['vth_V']], zero_base=True)
eeplot.place_label_box(axes[1])
eeplot.clean_xlim_time(axes[-1], t, unit='µs')
axes[-1].set_xlim(-.1, 4)
axes[-1].set_xticks([0, 1, 2, 3])
eeplot.save_fig(fig, out/'timer_12V.png', style='paper')

print('[pe-plots] type: A4; gallery: KIND=waveform-stack -> dab_exp_load_step')
d = eeplot.load_series(root/'data/line_step.csv')
t = d['time_s']*1e6
fig, axes = eeplot.waveform_axes(2)
axes[0].plot(t, d['vout_V'], color=eeplot.SYSTEM, lw=.7)
eeplot.channel_label(axes[0], r'$v_o$ [V]')
eeplot.clean_ylim(axes[0], data=d['vout_V'])
axes[0].axhline(0, color=eeplot.GRAY, lw=.5)
q = d['gh_V']
rising = None
pulse_times, pulse_widths = [], []
for index in range(1, len(q)):
    if q[index-1] < 2.5 <= q[index]:
        rising = d['time_s'][index]
    if q[index-1] >= 2.5 > q[index] and rising is not None:
        pulse_times.append(rising*1e6)
        pulse_widths.append((d['time_s'][index]-rising)*1e9)
        rising = None
axes[1].plot(pulse_times, pulse_widths, color=eeplot.SYSTEM, lw=.7)
eeplot.channel_label(axes[1], r'$T_{on}$ [ns]')
eeplot.clean_ylim(axes[1], data=pulse_widths, zero_base=True)
eeplot.clean_xlim_time(axes[-1], t, unit='µs')
axes[-1].set_xlim(-30, 630)
eeplot.event_line(axes, 200)
eeplot.event_line(axes, 400)
eeplot.save_fig(fig, out/'line_step.png', style='paper')

print('[pe-plots] type: D1; gallery: KIND=bode-stack -> loop_bode')
fig, (mag, phase) = eeplot.bode_axes()
for voltage, color, style in [(5, '#c62828', '-'), (18, '#238443', '--')]:
    d = eeplot.load_series(root/f'data/ac_{voltage}V.csv')
    for ax, column in [(mag, 'magnitude_dB'), (phase, 'phase_deg')]:
        ax.semilogx(d['frequency_Hz'], d[column], color=color, ls=style,
                    label=f'$V_{{in}}={voltage}$ V')
mag.set_ylabel('Magnitude [dB]')
phase.set_ylabel('Phase [deg]')
phase.set_xlabel('Frequency [Hz]')
mag.axhline(0, color=eeplot.GRAY, lw=.5)
phase.axhline(-180, color=eeplot.GRAY, lw=.5)
eeplot.clean_ylim(mag, data=np.concatenate([line.get_ydata() for line in mag.lines]), step=20)
eeplot.clean_ylim(phase, data=np.concatenate([line.get_ydata() for line in phase.lines]), step=90)
eeplot.place_label_box(mag)
phase.set_xlim(1e3, 5e6)
eeplot.save_fig(fig, out/'bode_5_18V.png', style='paper')
