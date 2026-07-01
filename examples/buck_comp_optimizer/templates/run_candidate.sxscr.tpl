Unset EchoOn
ClearMessageWindow

Let echo_file = OpenEchoFile('{{RUN_LOG}}', 'w')
Echo run_id={{RUN_ID}}
{{PARAM_LOG_LINES}}
Let close_result = CloseEchoFile()

OpenSchem /cd /readonly "{{SCHEMATIC}}"
simplis_run

Let sim_exit_code = GetSIMPLISExitCode()
Let echo_file = OpenEchoFile('{{SIM_STATUS}}', 'w')
Echo simplis_exit_code={sim_exit_code}
Echo simulation_errors=
Let close_result = CloseEchoFile()

Quit
