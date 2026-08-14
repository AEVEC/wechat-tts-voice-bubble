@echo off
setlocal
set "PYTHON_EXE=C:\Users\win\miniforge3\envs\wechat-tts-voice\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Error: Conda environment wechat-tts-voice was not found.
  exit /b 1
)

"%PYTHON_EXE%" "%~dp0wechat_tts_voice.py" %*
