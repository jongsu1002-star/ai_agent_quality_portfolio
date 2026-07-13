@echo off
REM 더블클릭으로 플랫폼 전체를 띄우는 실행 버튼.
REM Prometheus/Grafana(Docker, best-effort)를 먼저 기동한 뒤 FastAPI 앱을 실행합니다.
REM Docker가 없거나 꺼져 있어도 앱은 정상적으로 뜹니다 (scripts/start_platform.py 참고).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [start.bat] .venv\Scripts\python.exe 를 찾을 수 없습니다.
    echo [start.bat] 먼저 가상환경을 만들어주세요: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" scripts\start_platform.py
pause
