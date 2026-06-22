# EventSlotarr

Dispatcharr plugin for assigning temporary event streams into fixed placeholder channels.

## Current capabilities

- Parse provider event channels like `19:00 - Brazil x Spain [HD]`
- Group duplicate qualities by event
- Prefer HD, then HEVC/H265, then FHD, 1080, 720, SD
- Assign only currently active events into fixed placeholder channels
- Sticky slots so events do not jump around
- Optional auto-created EventSlotarr channels
- Optional source group auto-discovery
- Optional dynamic XMLTV output
- Persistent state in `data/*.json`

## Basic setup

1. Copy the `EventSlotarr` folder into your Dispatcharr plugins folder.
2. Restart Dispatcharr.
3. Create placeholder channels or enable auto-create channels.
4. Configure source groups such as `Canais | Jogos do Dia`.
5. Press `Assign Events` or configure scheduler with `Update Schedule`.

## Provider channel format expected

`HH:MM - Event Name [Quality]`

Examples:

- `16:00 - Estados Unidos x Austrália [HD]`
- `19:00 - Brasil x Espanha [FHD]`
- `22:00 - UFC Main Card [HD]`

## Important

This is an initial generated plugin. Test in a non-critical profile first.
