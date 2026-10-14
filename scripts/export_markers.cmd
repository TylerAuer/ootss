rem @echo off

for /f "usebackq delims=" %%a in (".env") do set %%a

set package=%GAME_DIR%\data\levels.package
set entities=%EXPORT_DIR%\data-common\overworld.entities
set level_sets=%EXPORT_DIR%\data\level_sets

if not exist "%out%" (
    python simp_tool.py extract "%package%" --output-dir "%out%"
)

python export_markers.py "%entities%" markers.geojson -x Inanimate -x GAME_PROM_Bars_Straight_MOR_A -x Wall_Set -x Rock -x Fence -x Decal -x Group -x Procedurally_Animating -x Point_Light -x Light_Probe -x Particle_Source

python enrich_markers.py -m markers.geojson -o markers.geojson %level_sets%


