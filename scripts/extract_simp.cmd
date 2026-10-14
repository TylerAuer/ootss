@echo off

for /f "usebackq delims=" %%a in (".env") do set %%a

set package=%GAME_DIR%\data\levels.package
set out=%EXPORT_DIR%
set exe=%GAME_DIR%\sinking_star.exe
set ent=%EXPORT_DIR%\entities_out

if not exist %out% (
    python simp_tool.py extract "%package%" --output-dir %out%
)

rem python simp_tool.py entities "%package%" --entry overworld.entities --contains heroes1_1
rem python simp_tool.py entities "%package%" --entry overworld.entities --contains 3d_intro_heroes --text
rem python simp_tool.py entities "%package%" --entry overworld.entities --list > entities.txt
rem python simp_tool2.py entities "%package%" --entry overworld.entities --text --contains GAME_OVER_Spawner_A
rem python simp_tool.py entities "%package%" --entry overworld.entities --text --contains GAME_EX_CamControl_Map_Pin_A

if not exist schema.json (
    python simp_tool.py schema "%exe%" -o schema.json
)

python simp_tool.py entities "%package%" --entry overworld.entities --text --contains GAME_EX_CamControl_Map_Pin_A --schema schema.json --output-dir "%ent%"

