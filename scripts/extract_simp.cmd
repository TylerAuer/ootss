@echo off

call all_vars.cmd

set package=%GAME_DIR%\data\levels.package
set exe=%GAME_DIR%\sinking_star.exe

python simp_tool.py extract "%package%"

rem python simp_tool.py entities "%package%" --entry overworld.entities --contains heroes1_1
rem python simp_tool.py entities "%package%" --entry overworld.entities --contains 3d_intro_heroes --text
rem python simp_tool.py entities "%package%" --entry overworld.entities --list > entities.txt
rem python simp_tool2.py entities "%package%" --entry overworld.entities --text --contains GAME_OVER_Spawner_A
rem python simp_tool.py entities "%package%" --entry overworld.entities --text --contains GAME_EX_CamControl_Map_Pin_A

python simp_tool.py schema "%exe%" -o schema.json
python simp_tool.py entities "%package%" --entry overworld.entities --text --contains GAME_EX_CamControl_Map_Pin_A --schema schema.json

