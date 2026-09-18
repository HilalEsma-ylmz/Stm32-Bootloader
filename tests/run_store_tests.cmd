@echo off
setlocal
cd /d "%~dp0.."
if not exist build mkdir build
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
cl /nologo /std:c11 /O2 /W4 /Ibootloader_stm32\Core\Inc tests\store_powercut.c bootloader_stm32\Core\Src\firmware_store.c /Fobuild\ /Febuild\store_powercut.exe
if errorlevel 1 exit /b 1
build\store_powercut.exe
