param([int]$Port = 18190)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

python "scripts\instagram_login_helper.py" --project-root $ProjectRoot --port $Port
