@echo off
rem Command-line batch mode, e.g.:  sync.bat "D:\Music\Some Album" -m large -w
cd /d "%~dp0"
.venv\Scripts\python.exe -m lyricsync %*
