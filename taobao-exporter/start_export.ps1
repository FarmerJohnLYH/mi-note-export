$ErrorActionPreference = 'Stop'
$wd = 'C:\Users\Administrator\Desktop\WorkDir\mi-note-export\taobao-exporter'
$py = "$wd\.venv\Scripts\python.exe"
$args = '-u taobao_order_exporter.py --non-interactive --auto-next --output C:\Users\Administrator\Desktop\WorkDir\mi-note-export\taobao-exporter\taobao_orders.xlsx --format xlsx --max-pages 200 --delay-min 8 --delay-max 20 --pause-every 10 --debug'
$p = Start-Process -FilePath $py -ArgumentList $args -WorkingDirectory $wd -RedirectStandardOutput "$wd\export.log" -RedirectStandardError "$wd\export.err.log" -PassThru
Write-Output "PID=$($p.Id)"
