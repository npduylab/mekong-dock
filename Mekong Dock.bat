@echo off
rem Mo Mekong Dock (thanh ung dung kieu macOS o mep tren man hinh, tkinter, khong can exe)
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw "%~dp0mac_dock.pyw") || (start "" "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" "%~dp0mac_dock.pyw")
