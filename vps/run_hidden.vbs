' run_hidden.vbs
' Run a .bat file with no console window (WindowStyle=0).
' Usage: wscript.exe //nologo run_hidden.vbs "path\to\script.bat"
'
' WHY THIS EXISTS:
'   Task Scheduler with /it (interactive session) shows a cmd.exe window each time
'   a .bat file is executed. WScript.Shell.Run with intWindowStyle=0 suppresses it
'   while keeping the process in the same interactive session (required for MT5 IPC).

If WScript.Arguments.Count = 0 Then
    WScript.Echo "Usage: wscript.exe run_hidden.vbs <bat_file_path>"
    WScript.Quit 1
End If

Dim WshShell
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """" & WScript.Arguments(0) & """", 0, False
WScript.Quit 0
