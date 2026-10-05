@echo off
REM Makes Part 2 and the Final Part of the laundry story for Storytime Cuts.
cd /d "%~dp0"
REM Prefer the "py" launcher: plain "python" can hit the Microsoft Store placeholder.
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
echo ==== Making Part 2 ====
%PY% run.py generate --channel storytime_cuts --script scripts/laundry_part2.json
echo.
echo ==== Making Final Part ====
%PY% run.py generate --channel storytime_cuts --script scripts/laundry_part3_final.json
echo.
echo ==== Done. Both videos are in output\review\storytime_cuts ====
echo Tell Claude "done" so it can stitch the full story.
echo If you see an error above, copy it and send it to Claude.
pause
