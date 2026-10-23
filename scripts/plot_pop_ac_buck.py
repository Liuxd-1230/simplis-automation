import argparse
from pathlib import Path
import sys

import numpy as np

parser = argparse.ArgumentParser(description="Plot measured native Buck validation CSV files")
parser.add_argument("--results", type=Path, required=True)
parser.add_argument("--eeplot-assets", type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.eeplot_assets.resolve()))
import eeplot

root = args.results.resolve()
out = root / "figures"
out.mkdir(exist_ok=True)
eeplot.use_style("paper", width="single")
print("[pe-plots] type: D1 — real SIMPLIS complex response; gallery: bode-stack")
data = eeplot.load_series(root/"data/ac.csv")
f = data["frequency_Hz"]
fig, (mag, phase) = eeplot.bode_axes()
mag.semilogx(f, data["magnitude_dB"], color=eeplot.SYSTEM)
mag.set_ylabel("Magnitude [dB]")
mag.axhline(0, color=eeplot.GRAY, lw=.5)
eeplot.clean_ylim(mag, data=data["magnitude_dB"], step=20)
phase.semilogx(f, data["phase_deg"], color=eeplot.SYSTEM)
phase.axhline(-180, color=eeplot.GRAY, lw=.5)
phase.set_ylabel("Phase [deg]")
phase.set_xlabel("Frequency [Hz]")
eeplot.clean_ylim(phase, data=data["phase_deg"], step=90)
phase.set_xlim(f[0], f[-1])
eeplot.save_fig(fig, out/"bode.png", style="paper")

print("[pe-plots] type: A2 — POP steady cycles; gallery: waveform-stack")
data = eeplot.load_series(root/"data/pop.csv")
t = data["time_s"]*1e6
# 对完整周期记录按时间积分；重复事件点的时间间隔为零
mean = np.dot(np.diff(data["time_s"]), (data["vout_V"][:-1]+data["vout_V"][1:])*.5)/(data["time_s"][-1]-data["time_s"][0])
channels = [([data["gh_V"],data["gl_V"]], r"$v_{GH},v_{GL}$ [V]"),
            ([data["sw_V"]], r"$v_{SW}$ [V]"),
            ([data["il_A"]], r"$i_L$ [A]"),
            ([(data["vout_V"]-mean)*1e3], r"$\Delta v_o$ [mV]")]
fig, axes = eeplot.waveform_axes(4)
for ax, (traces,label) in zip(axes,channels):
    for i,y in enumerate(traces):
        ax.plot(t,y,color=eeplot.phase_colors(2)[i] if len(traces)>1 else eeplot.SYSTEM,
                lw=.8,label=(r"$v_{GH}$",r"$v_{GL}$")[i] if len(traces)>1 else None)
    eeplot.channel_label(ax,label)
    eeplot.clean_ylim(ax,data=traces,sym=label.startswith(r"$\Delta"),zero_base=(label in {r"$v_{GH},v_{GL}$ [V]", r"$i_L$ [A]"}))
    if len(traces)>1: eeplot.place_label_box(ax)
eeplot.clean_xlim_time(axes[-1],t,unit="µs")
eeplot.save_fig(fig,out/"pop_cycles.png",style="paper")

print("[pe-plots] type: A4 — cold start and load step; gallery: waveform-stack")
data = eeplot.load_series(root/"data/startup.csv")
t = data["time_s"]*1e6
fig, axes = eeplot.waveform_axes(2)
axes[0].plot(t,data["vout_V"],color=eeplot.SYSTEM,lw=.8)
eeplot.channel_label(axes[0],r"$v_o$ [V]")
eeplot.clean_ylim(axes[0],data=data["vout_V"])
axes[0].axhline(0,color=eeplot.GRAY,lw=.5)
axes[1].plot(t,data["il_A"],color=eeplot.SYSTEM,lw=.6,label=r"$i_L$")
axes[1].plot(t,data["load_A"],color=eeplot.GRAY,lw=.8,ls="--",label=r"$i_o$")
eeplot.channel_label(axes[1],r"$i_L,i_o$ [A]")
eeplot.clean_ylim(axes[1],data=[data["il_A"],data["load_A"]],zero_base=True)
eeplot.place_label_box(axes[1])
eeplot.clean_xlim_time(axes[-1],t,unit="µs")
axes[-1].set_xlim(-20,420)
eeplot.save_fig(fig,out/"startup.png",style="paper")
