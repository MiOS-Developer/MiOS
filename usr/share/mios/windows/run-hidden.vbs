' MiOS Run-Hidden launcher: executes processes in hidden window mode without spawning console frames or Windows Terminal popups
Option Explicit
Dim WshShell, args, cmd, i, arg
Set WshShell = CreateObject("WScript.Shell")
Set args = WScript.Arguments
If args.Count > 0 Then
    cmd = ""
    For i = 0 To args.Count - 1
        arg = args(i)
        If InStr(arg, " ") > 0 And Left(arg, 1) <> """" Then
            arg = """" & arg & """"
        End If
        If cmd = "" Then
            cmd = arg
        Else
            cmd = cmd & " " & arg
        End If
    Next
    WshShell.Run cmd, 0, False
End If
