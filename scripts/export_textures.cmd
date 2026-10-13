@echo off
setlocal enabledelayedexpansion

call all_vars.cmd

set "base=%GAME_DIR%\data-pc"

for %%L in (
    worlds_collide_heroes_and_promesst_v4
    worlds_collide_ps
    skipping_promesst
    one_of_each
    blue_revolution_v2
    oneway_dragon_toast
    sp_crystal_tuto
    worlds_collide_heroes_and_stones
) do (
    set "file=!base!\__%%L.texture"

    if exist "!file!" (
        python jai_texture_to_png.py "!file!"
    )
)
