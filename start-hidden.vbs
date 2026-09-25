' gussari - hidden launcher
' Runs "python -u server.py" in a hidden window, output goes to server.log
' Port is passed via PORT env var (server.py: os.environ PORT || 3366)
' -u = unbuffered: banner must be flushed to server.log for stop.bat port parsing
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

port = ""
If WScript.Arguments.Count > 0 Then port = Trim(WScript.Arguments(0))
If port <> "" Then sh.Environment("PROCESS")("PORT") = port

Set lf = fso.OpenTextFile(dir & "\server.log", 8, True)
lf.WriteLine Now & " starting gussari (hidden), PORT=" & port
lf.Close

cmd = "cmd /c python -u server.py >> """ & dir & "\server.log"" 2>&1"
sh.Run cmd, 0, False
