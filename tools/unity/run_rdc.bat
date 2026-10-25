@echo off
rem run_rdc.bat "<capture folder>" [unity_rdc.py options]
rem   a folder that holds .rdc files (Unity game B's Renderdpc): that folder
rem   a folder of effect folders (Unity game A's Renderdoc): every subfolder that holds .rdc files
rem Needs Python 3 with numpy and Pillow; set UNITYFX_PYTHON to use another interpreter than "python".
setlocal
set "HERE=%~dp0"
if not defined UNITYFX_PYTHON set "UNITYFX_PYTHON=python"
set "TARGET=%~1"
if not defined TARGET goto usage
set "REST="
:collect
shift
if "%~1"=="" goto run
set REST=%REST% %1
goto collect

:run
if not exist "%TARGET%\*.rdc" goto every
%UNITYFX_PYTHON% "%HERE%unity_rdc.py" "%TARGET%" %REST%
exit /b %ERRORLEVEL%

:every
set "FOUND="
set "FAILED="
for /d %%D in ("%TARGET%\*") do (
  if exist "%%~D\*.rdc" (
    set "FOUND=1"
    %UNITYFX_PYTHON% "%HERE%unity_rdc.py" "%%~D" %REST%
    if errorlevel 1 set "FAILED=1"
  )
)
if not defined FOUND echo No .rdc files in "%TARGET%" or its subfolders
if not defined FOUND exit /b 2
if defined FAILED exit /b 1
exit /b 0

:usage
echo usage: run_rdc.bat "<capture folder>" [options]
echo options: python "%HERE%unity_rdc.py" --help
pause
exit /b 2
