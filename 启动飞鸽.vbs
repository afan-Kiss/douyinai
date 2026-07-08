Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root

ps = "Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
sh.Run "powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -Command """ & ps & """", 0, True

exe = root & "\dist\pigeon-feige.exe"
If Not fso.FileExists(exe) Then
  MsgBox "未找到 " & exe, vbCritical, "抖店 AI 客服工作台"
  WScript.Quit 1
End If

sh.Environment("Process")("PIGEON_PROJECT_ROOT") = root
sh.Environment("Process")("PIGEON_ROOT") = root
sh.Environment("Process")("PIGEON_NO_CDP") = "1"
sh.Environment("Process")("PIGEON_WS_HOST") = "jinritemai"
sh.Run """" & exe & """", 0, False
