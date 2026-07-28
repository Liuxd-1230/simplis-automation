# Buck PMIC Research Validation

For ACOT buck with Vramp-valley and a slow servo, validate innovation claims against pain points:

- Low-ESR stability without excessive artificial ramp.
- DC regulation error caused by comparator/ramp/valley offset.
- Startup oscillation or integrator windup.
- Load transient undershoot/overshoot and recovery.
- Switching period jitter and frequency drift.
- Robustness across Vin, load, ESR, Cout, ramp gain, and servo bandwidth.

For the user's current architecture:

```text
FB -> OTA integrator -> Vservo
FB + Vservo -> comparator negative
Vrefnew / ValleyVramp -> comparator positive
```

Potential comparative architecture:

```text
Fast ACOT path: FB + Vramp -> comparator negative
Reference path: Vref + ValleyVramp + Vtrim -> comparator positive
Slow servo: avg(FB)-Vref -> OTA/integrator -> anti-windup/slew-limit -> Vtrim
```

When simulating, compare at least:

- existing `FB + Vservo` injection
- decoupled `Vtrim` injection on reference/valley path
- anti-windup startup precharge/release
- transient freeze without resetting the integrator
- minimum-ramp adaptive clamp

Evidence needed for a paper-level claim:

- same external LC/load/ESR/Vin conditions
- same comparator/on-time/ramp nonidealities
- scalar table plus waveforms for startup and load transient
- corner sweep showing the new method reduces a real design tradeoff, not only one hand-picked case
