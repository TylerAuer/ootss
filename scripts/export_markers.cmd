@echo off

set package=E:\Games\Order of the Sinking Star\data\levels.package

set entities=D:\Projects\github\maps\submodules\ootss\scripts\unpacked\data-common\overworld.entities

rem python simp_tool.py extract "%package%"

rem python simp_tool.py entities "%package%" --entry overworld.entities --contains heroes1_1
rem python simp_tool.py entities "%package%" --entry overworld.entities --contains 3d_intro_heroes --text


rem python entities_geojson.py "%entities%" markers.geojson Level_Entry Monster Gem Sign Spawner Teleporter

python export_markers.py "%entities%" markers.geojson -x Inanimate -x GAME_PROM_Bars_Straight_MOR_A -x Wall_Set



