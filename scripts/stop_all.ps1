<#
.SYNOPSIS
  Stops every ScriptSync process (brand agents, impostors, assistant, web UI).
.DESCRIPTION
  Only touches processes that are ours: anything listening on the demo ports, plus python
  processes whose command line is one of our services. It never stops unrelated Python.
#>
$ports = 5500, 8080, 9001, 9002, 9101, 9102, 9103, 9104

# 1) whatever is listening on our ports (on Windows this is the venv's child process)
Get-NetTCPConnection -State Listen -LocalPort $ports -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

# 2) the venv launcher stubs (they can outlive the child), matched by command line
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'brand_agent|dev\.mock_agent|uvicorn assistant\.server|http\.server 5500' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 800
$left = Get-NetTCPConnection -State Listen -LocalPort $ports -ErrorAction SilentlyContinue
if ($left) { Write-Host "Still listening: $(($left.LocalPort | Sort-Object -Unique) -join ', ')" -ForegroundColor Yellow }
else { Write-Host "ScriptSync stopped." }
