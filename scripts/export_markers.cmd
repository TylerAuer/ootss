@echo off

set package=E:\Games\Order of the Sinking Star\data\levels.package
set entities=D:\Projects\github\maps\submodules\ootss\scripts\unpacked\data-common\overworld.entities

python export_markers.py "%entities%" markers.geojson -x Inanimate -x GAME_PROM_Bars_Straight_MOR_A -x Wall_Set -x Rock -x Fence -x Decal -x Group -x Procedurally_Animating -x Point_Light -x Light_Probe -x Particle_Source

python enrich_markers.py

move /Y markers_enriched.geojson markers.geojson




