# SIMetrix/SIMPLIS 8.4 Script Commands

## Launching SIMetrix

Use Windows command line for interactive inspection:

```powershell
& "D:\Simplis8.4\bin64\SIMetrix.exe" /i /s "path\to\startup.sxscr"
```

Known command-line pattern from SIMetrix docs:

```text
SIMetrix.exe [schematic_file] /s startup_script /i
SIMetrix.exe /com ...
```

`/i` keeps the session interactive. For automation, omit `/i` and end the script with `Quit` after saving all outputs.

## Schematic Creation

Useful commands from the installed Script Reference:

```text
NewSchem [/newWindow] [/simulator SIMPLIS|SIMetrix] <window-title>
OpenSchem [/cd] [/readonly] [/backup] filename
Inst [/loc <x> <y> <orient>] <symbolname> [propname] [propvalue]
Wire [/loc <x1> <y1> <x2> <y2>]
SaveAs [/force] [/binary] [/writeSymbol] [/tab <tabnum>] [/id id] <filename>
ScreenShotWindow <filename>
OpenEchoFile
CloseEchoFile
Quit
```

Notes:

- `NewSchem` creates a sheet but no file until `SaveAs`.
- `Inst /loc` is non-interactive. Coordinates are relative and often need inspection.
- `Wire /loc` draws a non-interactive wire segment.
- Use `SaveAs /force` during automation to avoid overwrite prompts.
- Add `Quit` for batch jobs after all files are saved; omit it only when the user wants to inspect the GUI.
- For generated schematics, use `scripts/schematic_generator.py` or `simplis_cli.py generate-schematic` with a YAML/JSON spec. It emits `Inst /loc` commands for devices and `term VALUE <netname>` terminals at each parsed pin location, then optionally runs `Netlist /simplis` for connectivity validation.

## Running SIMPLIS

For raw decks:

```text
RunSIMPLIS [/fresh] [/append] [/label label] filename
```

`RunSIMPLIS` does not preprocess. For a schematic-generated SIMPLIS netlist:

```text
Netlist /simplis design.net
PreProcessNetlist design.net design.deck
RunSIMPLIS /fresh design.deck
```

For GUI-equivalent schematic runs:

```text
OpenSchem "path\to\design.sxsch"
simplis_run
Quit
```

Use `simplis_run` when POP trigger resolution, schematic preprocessing, and normal GUI run behavior matter.

## Screenshots, Text, and Annotations

In the installed 8.4 Script Reference, `ScreenShotWindow` captures the current window to the clipboard. Do not assume `ScreenShotWindow "file.png"` writes a PNG unless proven in the local session.

The `AddFreeText` command applies to the selected graph, not schematic annotations. For schematic notes, prefer a known annotation/caption symbol only after verifying it exists in the installed symbol library. For robust proof-of-control, drawing wires and saving the schematic is enough.

## Local Documentation

Installed files worth searching:

```text
D:\Simplis8.4\support\docs\ScriptReference.pdf
D:\Simplis8.4\support\help\scriptdocumentedCommands.txt
D:\Simplis8.4\support\help\scriptdocumentedFunctions.txt
D:\Simplis8.4\support\dvm
D:\Simplis8.4\support\examples\SIMPLIS
```
