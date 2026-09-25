@echo off
rem Stop background gussari: parse actual port from server.log, kill by port
powershell -NoProfile -Command "$log = Get-Content '%~dp0server.log' -Raw -Encoding UTF8 -ErrorAction Stop; $m = [regex]::Matches($log, '127\.0\.0\.1:(\d+)') | Select-Object -Last 1; if (-not $m) { Write-Host 'port not found in server.log'; exit 1 }; $port = $m.Groups[1].Value; $conns = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue; if (-not $conns) { Write-Host ('gussari not running (port ' + $port + ' not listening)'); exit 0 }; $conns | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force; Write-Host ('stopped pid ' + $_ + ' (port ' + $port + ')') }"
pause
