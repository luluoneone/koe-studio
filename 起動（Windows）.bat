@echo off
chcp 65001 > nul
rem Koe Studio を起動する（ダブルクリック用）。止めるときはこのウィンドウで Ctrl+C。
cd /d "%~dp0"
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if exist "%UV%" goto run
where uv > NUL 2>&1
if not errorlevel 1 (set "UV=uv" & goto run)
echo 動かすのに必要な「uv」（Python の実行ツール）が入っていません。
set /p ans="公式の手順（https://docs.astral.sh/uv/）で今すぐ入れますか？ [y/N] "
if /i not "%ans%"=="y" goto cancel
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" goto fail
:run
"%UV%" run -q app.py
if errorlevel 1 pause
exit /b
:cancel
echo 中止しました。
pause
exit /b 1
:fail
echo uv を入れられませんでした。
pause
exit /b 1
