@echo off
REM Reproducible native Windows linking script for trg 0.15.0
setlocal enabledelayedexpansion

set ROOT=%~dp0..
cd /d %ROOT%

echo === Linking trg 0.15.0 on Windows ===

REM Compile UTF-8 and long-path manifest
windres src\c\trg.rc -O coff -o target\trg_manifest.o

REM Compile C compatibility bridge for x64
clang -c src\c\trg_win_compat.c -O2 -o target\trg_win_compat_x64.o

REM Link x64 binary
clang target\x86_64-pc-windows-gnu\trg.o target\trg_win_compat_x64.o target\trg_manifest.o toka_rt.o -o target\x86_64-pc-windows-gnu\trg.exe -lbcrypt -lws2_32

REM Compile C compatibility bridge for ARM64
clang -target aarch64-w64-windows-gnu -c src\c\trg_win_compat.c -O2 -o target\trg_win_compat_arm64.o

REM Link ARM64 binary
ld.lld -m arm64pe -o target\aarch64-pc-windows-gnu\trg_arm64.exe arm64_lib\crt2.o target\aarch64-pc-windows-gnu\trg.o target\trg_win_compat_arm64.o target\trg_manifest.o toka_rt_arm64.o -L arm64_lib arm64_lib\clang\22\lib\windows\libclang_rt.builtins-aarch64.a -lmingw32 -lmingwex -lmsvcrt -lkernel32 -lbcrypt -lws2_32 -lshell32

echo === Linking complete! ===
