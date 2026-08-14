Option Explicit

Dim shell, pythonw, script
Set shell = CreateObject("WScript.Shell")
pythonw = "C:\Users\win\miniforge3\envs\wechat-tts-voice\pythonw.exe"
script = "C:\Users\win\wechat-tts-voice\wechat_tts_widget.pyw"

shell.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False
