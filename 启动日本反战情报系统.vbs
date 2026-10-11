Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = fso.BuildPath(root, "scripts\Start-JapanAntiwarIntel.ps1")
shell.Run "powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """ -AutoStart", 0, False
