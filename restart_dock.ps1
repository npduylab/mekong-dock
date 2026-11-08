# Khoi dong lai Mekong Dock dung cach: bao Dock tu thoat (quit.flag) de no tra lai cho o mep tren man hinh,
# roi moi chay lai. Chi kill khi Dock khong tu thoat sau 10 giay.
$d = $PSScriptRoot
$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe" }
$find = { Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like '*mac_dock.pyw*' } }
if (& $find) {
    New-Item -ItemType File -Force (Join-Path $d "quit.flag") | Out-Null
    for ($i = 0; $i -lt 20 -and (& $find); $i++) { Start-Sleep -Milliseconds 500 }
    & $find | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
}
Remove-Item (Join-Path $d "quit.flag") -ErrorAction SilentlyContinue
Start-Process -FilePath $py -ArgumentList "`"$d\mac_dock.pyw`"" -WorkingDirectory $d
"Mekong Dock da khoi dong lai"
