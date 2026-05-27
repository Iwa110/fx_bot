' run_hidden.vbs
' Run a .bat file with no console window (WindowStyle=0), waiting for completion.
' Usage: wscript.exe //nologo run_hidden.vbs "path\to\script.bat"
'
' WHY THIS EXISTS:
'   Task Scheduler with /it (interactive session) shows a cmd.exe window each time
'   a .bat file is executed. WScript.Shell.Run with intWindowStyle=0 suppresses it
'   while keeping the process in the same interactive session (required for MT5 IPC).
'
' WHY bWaitOnReturn=True (NOT False):
'   With False (async), wscript.exe exits immediately after launching the bat.
'   Task Scheduler's Job Object then cleans up and force-kills all child processes
'   (cmd.exe -> python.exe), terminating the script mid-execution.
'   With True (sync), wscript.exe waits for the bat to finish before exiting,
'   so the Job Object stays alive until all work is complete.

If WScript.Arguments.Count = 0 Then
    WScript.Echo "Usage: wscript.exe run_hidden.vbs <bat_file_path>"
    WScript.Quit 1
End If

Dim WshShell
Set WshShell = CreateObject("WScript.Shell")
Dim exitCode
exitCode = WshShell.Run("""" & WScript.Arguments(0) & """", 0, True)
WScript.Quit exitCode
