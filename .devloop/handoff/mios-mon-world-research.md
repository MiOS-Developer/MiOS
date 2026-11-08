# Mon-World design research (deep-research run wf_0aea4bcf-f5c, 2026-11-07)

This was a cited, adversarially verified research run: 5 angles, 25 sources, 125 claims extracted, 25 verified by 3-vote refutation (20 confirmed, 5 refuted).
Findings marked RECOMMENDATION are design proposals derived from the verified facts, not findings.

## Summary

The verified evidence gives Mon-World a concrete Habbo-style room layer. In the Kepler v14-era emulator, a room is a text heightmap on a small tile grid. Guest models run from 12x16 to 29x30; the 12x16 model_a has 104 walkable tiles and the smallest, model_s, is 7x9 with 36. Guest rooms default to 25 visitors (owners can set 10-50) and public hubs go up to 100. Access is open, doorbell or password, plus a rights table, and rooms tick only while occupied: movement at 2 Hz (about 2 tiles/s), status at 1 Hz, minigames at 2-3.3 Hz. Classic SimCity supplies the "server = city" layer. Its map is a fixed 120x100 tiles (Classic) or 128x128 (SimCity 2000, inferred from file byte counts). The player sees a scrolled 16-px-per-tile partial view, moved by drag or edge auto-scroll, plus a 3-px-per-tile overview with a draggable viewport rectangle. Multi-player SimCity for X11 (1993) already let several players share one city with several views each, chat, annotation and voting. For rendering, prior art supports four techniques. The first streams an unbounded X/Z grid around the camera. The second uses two detail bands: full voxels nearby and pre-rendered snapshot impostors farther out. The third culls work to the visible screen, as in OpenTTD's isometric tile walk and cpuvox's per-column culling. The fourth is CPU column raycasting, which reaches real time at 1080p; that benchmark used all 4 cores plus a GPU blit, so the cost per instance at terminal resolution still has to be measured. Nothing was verified for the flat 2D 3x3 side-strip view, for container or static-binary hosting over LAN plus Tailscale, or for Habbo furni, Navigator, chat, trading, economy and pathfinding. Items marked RECOMMENDATION below are design proposals built on the verified facts, not findings.

## Verified findings and recommendations

### 1. [high confidence, vote 3-0]

In the Kepler emulator (v14-era Habbo), a room's floor plan is one text heightmap. Rows are separated by '|', 'x' marks a void tile, and a digit gives the tile's base floor height. Guest-room models model_a to model_r sit on grids from 12x16 up to 29x30 (model_m). model_a is 12x16 with 8x13 = 104 walkable tiles. model_s, Kepler's starter-room model, is the smallest grid at 7x9 with 36 walkable tiles.

Evidence: The verifier parsed all 91 heightmaps in rooms_models. model_a-f and model_h are 12x16, model_g is 13x17 and model_m is 29x30. The next-smallest grid after model_s is park_b at 12x6. model_c ties model_s at 36 tiles. RoomModel.java splits each heightmap on '|', marks non-numeric cells CLOSED and reads a numeric cell as the tile height. Arcturus Morningstar also treats 'x' as an invalid tile. Qualifications: the door tile (3,5) is forced open, so the server actually treats 105 tiles of model_a as walkable. The digit is the floor height, not the height of furni stacked on it. Normal users can create models a-f and i-n; g, h and o-r need the USE_SPECIAL_ROOM_LAYOUTS fuse. Later official Habbo added custom floor plans.

Sources: https://github.com/Quackster/Kepler/blob/master/tools/kepler.sql; Kepler-Server RoomModel.java (parse())

### 2. [high confidence, vote 3-0]

In Kepler, a new guest room gets a default capacity of 25 visitors. Owners can set 10 to 50, and any value outside that range is reset to 25. Public rooms are capped between 20 and 100: Main Lobby, Theatredome and the staff room The Den allow 100, most cafes and lounges 35-50, and every hallway and game hall 25.

Evidence: The schema declares `visitors_max int(11) DEFAULT 25`. NavigatorDao.createRoom never sets that column, so every new guest room gets the default. SETFLATINFO.java resets any value below 10 or above 50 to 25. The verifier parsed all 69 public-room rows. The minimum is 20 (TV Studio, Habbo Kitchen, Rooftop Cafe, Infobus). 8 of 11 cafes and lounges fall in 35-50; the exceptions are Rooftop Cafe (20), Ice Cafe (25) and Net Cafe (25). All 12 hallways and every category-13 game hall (BattleBall and SnowStorm lobbies, Chess, Poker and others) are 25. These are values chosen by the emulator authors, not confirmed Sulake values.

Sources: https://github.com/Quackster/Kepler/blob/master/tools/kepler.sql; Kepler CREATEFLAT.java / NavigatorDao.createRoom / SETFLATINFO.java

### 3. [high confidence, vote 3-0 (three claims merged)]

Room access is stored on each room row, with rights in a separate list. Each room stores an access type (0 = open, 1 = doorbell/locked, 2 = password) and a password string. Room rights are a separate (user_id, room_id) join table. Public rooms belong to owner '0', with access type 0 and an empty password. A doorbell room lets rights holders and staff straight in. For anyone else it sends DOORBELL_WAIT, carrying the visitor's name, to every rights holder in the room. If no rights holder is present, the visitor is refused at once (FLATNOTALLOWEDTOENTER) and is not queued. A password room lets in the owner, staff, or anyone who gives the matching password.

Evidence: The schema has `accesstype tinyint(3) DEFAULT 0`, `password varchar(255) DEFAULT ''`, and a two-column rooms_rights table. Room.isPublicRoom() returns true when ownerId == 0. RoomData.getAccessType() maps 2 to password, 1 to closed and anything else to open. TRYFLAT.java contains both checks word for word. The whole check is skipped for players with the ENTER_LOCKED_ROOMS fuse, and for players already authenticated for that room (for example, let in through the doorbell). The ANY_ROOM_CONTROLLER fuse also skips the password. hasRights() is true for the owner, for anyone on the rights list, and for everyone when allowSuperUsers is set. Arcturus independently does the same for RoomState.LOCKED and queues the visitor (addToQueue) only when a rights holder was found. That public rooms use access type 0 is a seed-data convention, not a schema constraint.

Sources: https://github.com/Quackster/Kepler/blob/master/tools/kepler.sql; https://github.com/Quackster/Kepler/blob/master/Kepler-Server/src/main/java/org/alexdev/kepler/messages/incoming/rooms/TRYFLAT.java; Arcturus Morningstar RoomManager.java (git.krews.org, cited by verifier)

### 4. [high confidence, vote 3-0]

Kepler ticks a room only while someone is in it, using fixed-rate tasks. EntityTask (avatar movement) runs every 500 ms and moves a walking avatar one tile per tick, about 2 tiles/s. StatusTask runs every 1 s. RollerTask runs only in private rooms, every roller.tick.default (2000 ms by default). In game rooms, the BattleBall task runs every 500 ms and the SnowStorm task every 300 ms. Tasks start when the first player enters and are cancelled when the room is disposed.

Evidence: The tasks are scheduled with scheduleAtFixedRate. RoomEntityManager.tryInitialiseRoom() calls startTasks() when the first player enters. stopTasks() runs from Room.tryDispose() after room.dispose.timer.seconds (60 by default), so an emptied room keeps ticking for about 60 s. Arena rooms (_arena_ / snowwar_arena) run only the game task. All rooms share one GameScheduler executor; there is no thread per room. The Kepler README says it emulates the 2007 v14 Habbo, not official Sulake code.

Sources: https://github.com/Quackster/Kepler (RoomTaskManager.java; the original citation to Pathfinder.java points at the wrong file); https://github.com/Quackster/Kepler/blob/master/Kepler-Server/src/main/java/org/alexdev/kepler/game/pathfinder/Pathfinder.java

### 5. [high confidence, vote 3-0 (two claims merged)]

Classic SimCity (Micropolis) uses a fixed city map of 120x100 tiles (12,000 tiles). A compile-time MEGA flag can multiply both sides, but no build defines it, and the newer C++ engine hard-codes WORLD_W = 120 and WORLD_H = 100. A SimCity 2000 (MS-DOS) map holds 16,384 squares, one byte per square in each layer. That makes it 128x128, a shape inferred from the byte counts and backed by other sources.

Evidence: sim.h contains `#define SimWidth 120` and `#define SimHeight 100` (with a MEGA variant), and map_type.h contains `WORLD_W = 120; WORLD_H = 100`. The CHM reverse-engineering diagrams list 120x100 as full scale. In the SC2000 file format, XTER, XBLD, XZON, XUND, XTXT and XBIT are each 16,384 bytes after decompression, with one byte per square. ALTM is 32,768 bytes at two bytes per square. The quarter-resolution layers are 4,096 bytes (64x64). The Ludo guide says SC2000 terrains are square with 128 tiles per side. A single hobby page claims 256x256; it matches neither the byte counts nor the other sources.

Sources: https://github.com/SimHacker/micropolis; https://www.djm.cc/simcity-2000-info.txt; smalltalkzoo.computerhistory.org/users/Dan/uploads/SimCityReverseDiagrams.pdf

### 6. [high confidence, vote 3-0 (three claims merged)]

SimCity Classic shows a large map through a scrolled partial view plus an overview minimap. The editor draws each tile at 16 px, so the whole city is 1920x1600 px, but only a window of it is on screen. That window scrolls by dragging with the middle mouse button or by auto-scroll at the edge. A separate Map Window draws the whole city at 3 px per tile (360x300 px). Each open editor view appears there as a yellow rectangle, and dragging the rectangle scrolls that view.

Evidence: sim.h defines EDITOR_W = WORLD_X*16 and MAP_W = WORLD_X*3. Tile placement in the code uses x<<4, which confirms 16-px tiles. w_editor.c pans with pan_x/pan_y and w_tk.c has AutoScrollEdge. w_map.c scales coordinates by 3/16, and g_smmaps.c writes 3 px per tile. The tutorial says: 'detailed map showing part of the terrain', 'scroll the map by pressing the middle mouse button down ... and dragging', and 'There may be more than one yellow rectangles, if multiple views are visible'. Qualifications: the claim that 1920x1600 was larger than any 1990s screen overreaches, since some workstation CRTs were 2048x2048, though it holds for typical consumer screens. All of this documents the Unix/X11 port; the DOS, Amiga and Mac versions may differ.

Sources: https://github.com/SimHacker/micropolis; https://sources.debian.org/src/micropolis-activity/0.0.20071228-7/manual/tutorial.html/

### 7. [high confidence, vote 3-0 (two claims merged)]

Multi-player SimCity for X11 (DUX Software, 1993; Unix port and multiplayer interface by Don Hopkins) let several people on different X11 workstations play in one shared city at the same time. Each player could open several city-editor views and map views with overlays. The game also had simultaneous editing, chat, chalk annotation, and voting dialogs for group decisions such as using a map, zoning and the budget.

Evidence: The announcement says: 'Several people on different X11 workstations can participate in the same city', with 'simultaneous multi player editing, communication and annotation facilities, and voting dialogs'. micropolis.tcl has AddPlayer {display}, NewEditorOf/NewMapOf {head}, SetEditorOverlay (population and pollution density), Chalk and Eraser tools, an IRC-style chat setting (ChatServer localhost, ChatSocket 6667), and vote state VotesForUseThisMap/Ask/Zone/Budget handled by DoVote. The FreeBSD 2.1.5 README and a 1997 Sun Solutions CD entry agree. The architecture caveat: one simulation process drew directly to each player's X display (man page: -m multi player, -w wire mode). It was not a dedicated server with thin clients, so it is a precedent for one shared city per process, not for a client/server game protocol.

Sources: https://www.cs.cmu.edu/Groups/AI/util/areas/speech/systems/netaudio/contrib/simcity.txt; https://github.com/SimHacker/micropolis (micropolis-activity/res/micropolis.tcl); manpages.ubuntu.com micropolis.6

### 8. [medium confidence, vote 3-0]

Prior art for an unlimited horizontal grid where only the area around the viewport is kept in memory: concinnity-core's VoxelWorld (version 0.19.119) is infinite on X/Z and one chunk tall on Y. Chunks are generated on demand from a seed and streamed in and out around the camera. The streaming radius is view_radius (default 5, clamped 0-32 chunks), and at most 3 chunks load per frame by default.

Evidence: The doc comments say: 'The grid is infinite on X/Z and a single chunk tall on Y' and chunks are 'generated on demand from `seed` as the camera moves and streamed in and out around it'. The same seed always regenerates the same chunk. chunk_blocks defaults to 16x24x16. In chunk_window.rs a chunk is either Pending or Resident, and anything outside the window counts as unloaded. Limits: this is a young GPU engine whose streaming currently feeds only its Metal path, and it is not a CPU or terminal renderer. It shows the pattern exists, not that it works at scale.

Sources: https://docs.rs/concinnity-core/latest/src/concinnity_core/components/voxel_world.rs.html; concinnity-render chunk_window.rs (sliding-window policy)

### 9. [high confidence, vote 3-0 (three claims merged)]

Two independent Rust voxel engines use the same two-band level of detail: full voxel geometry near the camera and cheap impostors farther out. In concinnity-core, chunks between view_radius and impostor_radius (clamped 0-96, never below view_radius, off by default) render as a low-detail surface mesh. roxlap-scene, a CPU renderer descended from Voxlap, does not rasterize a grid at Lod::Far. It draws one of 26 pre-rendered snapshots instead (6 face, 12 edge and 8 corner views, each 128x128 with 4 bytes of colour and 4 of depth, about 3.4 MB per grid). It picks the snapshot whose view direction has the largest dot product with the direction from the grid to the camera.

Evidence: roxlap's documentation says: 'At `Lod::Far` distance the per-grid rasterizer is too expensive and produces sub-pixel detail no one will ever see'. render.rs has an `if lod == Lod::Far` branch that calls billboard_blit_into and skips raycasting, and the test s6_3_far_tier_blits_non_sky_pixels covers it. The cache fills lazily and edits invalidate it. 26 x 128² x 8 bytes = 3,407,872 bytes. The same design is still in release 0.32.0 (2026-08-08), although that release replaced the Voxlap port with a clean-room 3D-DDA brickmap renderer. The far-tier dispatch exists only in the render_scene_composed* paths. concinnity's impostor_radius accessor clamps to 0-96 and then floors at view_radius. Both crates are young and small, so this shows one way to build it, not an industry standard.

Sources: https://docs.rs/concinnity-core/latest/src/concinnity_core/components/voxel_world.rs.html; https://docs.rs/roxlap-scene/0.4.2/src/roxlap_scene/billboard.rs.html

### 10. [medium confidence, vote 3-0]

Voxlap-style column raycasting on a CPU can run in real time at high resolution. cpuvox (C#, Unity Burst) casts one ray per screen pixel column through the XZ world into a raybuffer. Its author reports 10-60 fps at 1080p, depending on view, on a 4-core i5-4670K, for a 36.9-million-voxel scene in a 2048^3 world.

Evidence: README: 'renders at 10-60 fps at 1080p depending on view on an intel i5-4670k'. Limits: the figure is self-reported with no method given. The benchmark runs IJobParallelFor jobs on every core, so 10 fps is the floor on a fully loaded 4-core CPU. Phase 2, which projects the raybuffer onto the screen, runs on the GPU (RayBufferBlit.shader). The algorithm uses perspective and a vanishing point, not an orthographic isometric view. The idea that a terminal's far fewer columns cut the cost proportionally is inference: per-ray cost also depends on depth, draw distance and voxel density.

Sources: https://github.com/pipliz/cpuvox

### 11. [high confidence, vote 3-0 (two claims merged)]

Both prior-art renderers limit work to what the viewport captures. OpenTTD's CPU isometric viewport (ViewportAddLandscape) walks the tile grid in screen-space rows (x+y) and columns (y-x), which overlap by half a tile, starting one row above the upper-left corner. It skips any tile whose worst-case pixel extent (MAX_TILE_EXTENT_TOP/BOTTOM) falls outside the draw rectangle. In its 2:1 projection, each step in X or Y moves the screen position down by TILE_PIXELS/2. cpuvox clips the near and far vertical lines of each world column against the view frustum to get the visible min/max Y range. It ends a screen-column ray early, filling with skybox, once nothing more can be seen.

Evidence: OpenTTD master src/viewport.cpp computes `row = (upper_left.x + upper_left.y)/TILE_SIZE - 2` and has `if (viewport_y + MAX_TILE_EXTENT_BOTTOM < _vd.dpi.top) continue;`. MAX_TILE_EXTENT_TOP = ZOOM_BASE*MAX_BUILDING_PIXELS and MAX_TILE_EXTENT_BOTTOM = ZOOM_BASE*(TILE_PIXELS+2*TILE_HEIGHT). GetViewportY = tile.y*(TILE_PIXELS/2) + tile.x*(TILE_PIXELS/2) - height. Horizontal culling comes from left/right column bounds padded by 2 columns, and bridges widen the test. In cpuvox, the full clip test runs only until the frustum is first set; after that a cheaper slope test is used. The savings come from frustum culling and occlusion together.

Sources: https://docs.openttd.org/source/da/d7e/viewport_8cpp.html; https://docs.openttd.org/source/da/d7e/viewport_8cpp_source.html; https://github.com/pipliz/cpuvox (README Phase 1 step 3.4; Assets/Code/Rendering/DrawSegmentRayJob.cs)

### 12. [low confidence, vote n/a (synthesis)]

RECOMMENDATION (not verified; derived from the Kepler findings). Mon-World room layer: store each room as a heightmap-style tile grid in which the Mon default room is the unit cell. Use Kepler's numbers as starting points. Capacity: default 25, owner-settable 10-50, public hubs up to 100. Access: 0 open, 1 doorbell, 2 password, plus a separate (user, room) rights list. The server ticks movement at 2 Hz (2 tiles/s), status at 1 Hz and minigames at 2-3.3 Hz; clients interpolate and render at their own frame rate. Decide the doorbell policy explicitly: Kepler refuses at once when no rights holder is present, while Arcturus queues the visitor when one is.

Evidence: Synthesis only. At 2 tiles/s, crossing a 12x16 room along its long side takes about 8 s, which is Habbo's pacing. Server ticks at 1-3 Hz keep the per-room CPU cost on the server tiny, and voxel rendering stays on each viewer's terminal.

Sources: https://github.com/Quackster/Kepler/blob/master/tools/kepler.sql; https://github.com/Quackster/Kepler/blob/master/Kepler-Server/src/main/java/org/alexdev/kepler/messages/incoming/rooms/TRYFLAT.java

### 13. [low confidence, vote n/a (synthesis)]

RECOMMENDATION (not verified). Hosting cost model: tick only occupied rooms and dispose them after a grace period, as Kepler does with 60 s. Kepler runs all rooms on one shared scheduler, not a thread per room. To keep rooms as minimal containers, each room container should be stopped or socket-activated when empty, so an idle room costs about nothing. The cheaper alternative is many rooms per server process on a shared scheduler, with a container per server (city) rather than per room. Rendering stays client-side; the server sends only tile, entity and state changes.

Evidence: Synthesis only. At Kepler's rates, an occupied room costs about 3 scheduled task runs per second (2 for movement, 1 for status). 1,000 occupied rooms would mean about 3,000 task runs per second on a shared scheduler. No sources were verified for container overhead, static-binary distribution, or LAN/Tailscale-only access; measure memory and CPU per room before committing to one container per room.

Sources: https://github.com/Quackster/Kepler (RoomTaskManager.java, Room.tryDispose)

### 14. [low confidence, vote n/a (synthesis)]

RECOMMENDATION (not verified). Server = city, SimCity-style: each lot on a city grid is one room. A 120x100-lot city at one half-block pixel per lot fits a 120x50-cell terminal minimap exactly, and 128x128 needs 128x64 cells. Use the minimap as the Navigator: the viewport rectangle shows what is on screen and can be dragged to scroll, alongside edge-scrolling and drag-scrolling of the main isometric view. Allow several views per player. Borrow the shared-city tools from 1993 multiplayer SimCity: chat, annotation and voting for city-level decisions.

Evidence: Synthesis: 120 columns x (100/2) rows = 120x50 terminal cells using half-blocks. SimCity's map sizes were chosen for fixed 1990s maps; Mon-World's grid is unbounded, so the minimap would show a window around the player plus coordinates, not the whole world.

Sources: https://github.com/SimHacker/micropolis; https://sources.debian.org/src/micropolis-activity/0.0.20071228-7/manual/tutorial.html/; https://www.cs.cmu.edu/Groups/AI/util/areas/speech/systems/netaudio/contrib/simcity.txt

### 15. [low confidence, vote n/a (synthesis)]

RECOMMENDATION (not verified). Isometric voxel pipeline in the terminal: render the player's room and its 8 neighbours (3x3) as full voxels. Beyond that, out to the edge of the viewport, use impostors. Regenerate rooms deterministically from their stored data and cap how many load per frame. Cull with an OpenTTD-style row/column walk whose worst-case extent is the tallest possible room. A fixed isometric camera with 4 rotations needs only 4 snapshots per room, not roxlap's 26.

Evidence: Synthesis arithmetic. At 4 snapshots x 32x32 px x 8 bytes, an impostor costs about 32 KB per room, against 3.4 MB for roxlap's default. 100 impostor rooms would be about 3.2 MB per client. A 200x50-cell terminal is 200x100 px in half-block, 400x100 in quadrant and 400x200 in braille, against 2,073,600 px at 1080p: about 25-100x fewer pixels and 5-10x fewer columns. Cost does not scale linearly with that, so benchmark on the target host with several instances running at once.

Sources: https://docs.rs/roxlap-scene/0.4.2/src/roxlap_scene/billboard.rs.html; https://docs.rs/concinnity-core/latest/src/concinnity_core/components/voxel_world.rs.html; https://docs.openttd.org/source/da/d7e/viewport_8cpp.html; https://github.com/pipliz/cpuvox

### 16. [low confidence, vote n/a (synthesis)]

RECOMMENDATION (no prior art verified). Flat 2D side view: flatten a 3x3-room block horizontally into a strip. The world's depth axis (front to back) becomes a small number of depth lanes drawn back to front, so mons visibly walk toward or away from the viewer. The view scrolls horizontally with a dead zone to follow the player's mon(s). The strip reuses the same room data and server ticks as the isometric view, so switching views changes only the client.

Evidence: None of the research claims on belt-scroller depth lanes, side-scrolling social games or Habbo-like 2.5D side views survived verification. Treat this purely as a design proposal until prior art is checked.

Sources: No verified source; design proposal

## Caveats

Every Habbo fact here comes from Kepler, a community emulator of the 2007 v14 client, and is corroborated in places by Arcturus Morningstar. None of it is Sulake's official server code. Room capacities and model choices are the emulator authors' values, and later official Habbo added custom floor plans. The tick-rate claim quoted RoomTaskManager.java but cited Pathfinder.java; the numbers are verified, the citation was wrong. Kepler rooms keep ticking for about 60 s after they empty, and arena rooms run only the game task.

Many parts of the research question have no verified evidence at all. On the Habbo side: furni placement, stacking and rotation, the Navigator UI, chat bubbles, trading, Wobble Squabble, Wired, achievements, the economy, and the exact isometric camera. Also missing: pathfinding (both Kepler pathfinder claims were refuted 0-3), prior art for 2D depth-lane side views, and hosting as containers or a static binary over LAN plus Tailscale.

Refuted claims that must not be used: Kepler's step-height limits and its non-A* search details; Micropolis storing overlay data in 2x2/4x4/8x8 grids; cpuvox using run-length-compressed voxel columns; and OpenTTD's bounding-box sprite-sort details.

SimCity facts come from Micropolis, the GPL release of the Unix/X11 port; the DOS, Amiga and Mac versions may differ. SimCity 2000's 128x128 size is inferred from file byte counts plus secondary sources. 1993 multiplayer SimCity was one process drawing to several X displays, not a client/server design.

Renderer prior art is thin. concinnity is a young GPU (Metal) engine. roxlap is a small hobby crate whose renderer was rewritten between 0.4.2 and 0.32.0. cpuvox's benchmark is self-reported, used all 4 cores and blits on the GPU, and uses perspective rather than isometric projection, so no performance number carries over directly to a CPU-only terminal renderer with many instances.

Time-sensitivity: the crate versions are from 2026 (concinnity 0.19.119, roxlap 0.4.2 and 0.32.0), and the Kepler and OpenTTD sources were read on master as of 2026-11-07; later releases may change details. All numbers in RECOMMENDATION findings are synthesis arithmetic, not measurements.

## Open questions (next research or measurement)

- How did Habbo (or Kepler/Arcturus) handle furni stacking height, rotation steps, the Navigator's categories and search, chat bubbles, trading, Wired triggers and the credit economy? A targeted code read of Kepler or Arcturus item, navigator and trade handlers is needed before Mon-World copies these mechanics.
- What do per-room minimal containers actually cost (RSS, idle CPU, cold-start time when socket-activated) compared with many rooms in one Rust process on a shared scheduler? And how are rooms discovered and joined over LAN plus Tailscale only, with a single static binary or a container image as the client and server?
- Is there real prior art for the flat 2D 3x3 side strip, such as belt-scroller depth lanes or 2.5D social side-scrollers, and how should several followed mons share one horizontal scroll position?
- What is the measured cost per frame and per instance of a CPU orthographic isometric voxel renderer at half-block, quadrant and braille resolutions with a 3x3 full-detail area plus an impostor ring, and which click-to-move pathfinding algorithm fits once the Kepler pathfinder claims are set aside?

## Refuted (do NOT use)

- (0-3) Kepler's click-to-move pathfinder limits height change per step to an upward lift of 1.5 and a downward drop of 3.0 (in tile-height units). Teleporters, queue tiles, pool lifts and pool enter/exit pairs are exempt from these checks. [https://github.com/Quackster/Kepler/blob/master/Kepler-Server/src/main/java/org/alexdev/kepler/game/pathfinder/Pathfinder.java]
- (0-3) The search is not textbook A*. Step cost is 2 per changed axis (4 for a diagonal) plus the squared Euclidean distance to the goal. The open list is a FIFO LinkedList polled from the front, not a priority queue, with no iteration cap. The search runs from goal to start and is sized to the room model's map dimensions (getMapSizeX by getMapSizeY). [https://github.com/Quackster/Kepler/blob/master/Kepler-Server/src/main/java/org/alexdev/kepler/game/pathfinder/Pathfinder.java]
- (0-3) Simulation and overlay data are not kept per tile. They are stored in coarser grids at 2x2 (60x50), 4x4 (30x25) and 8x8 (15x13) tile blocks: population density, traffic, pollution, land value and crime at 2x2; terrain at 4x4; fire and police coverage at 8x8. This multi-resolution layering is an early example of level of detail for large-map state. [https://github.com/SimHacker/micropolis]
- (0-3) Voxel world storage uses one run-length compressed list of solid voxels per XZ column. This kept a sample scene of about 36.9 million voxels in a 2048^3 world renderable on a CPU. RLE columns are a workable memory and traversal format for a large tiled voxel grid. [https://github.com/pipliz/cpuvox]
- (0-3) Depth ordering of objects (parent sprites) uses 3D axis-aligned bounding boxes. Two sprites are ordered against each other only when their boxes overlap in X, Y and Z. The sorter prunes candidates by comparing xmin+ymin against xmax+ymax and leaves Z out of that sum. It also assumes the input is already mostly in order, which keeps sorting cheap. [https://docs.openttd.org/source/da/d7e/viewport_8cpp.html]

## Sources

- [primary] https://github.com/Quackster/Kepler/blob/master/tools/kepler.sql (Habbo Hotel room and social mechanics (primary))
- [primary] https://github.com/Quackster/Kepler/blob/master/Kepler-Server/src/main/java/org/alexdev/kepler/game/pathfinder/Pathfinder.java (Habbo Hotel room and social mechanics (primary))
- [primary] https://github.com/Quackster/Kepler/blob/master/Kepler-Server/src/main/java/org/alexdev/kepler/messages/incoming/rooms/TRYFLAT.java (Habbo Hotel room and social mechanics (primary))
- [secondary] https://habbo.fandom.com/wiki/Guest_room (Habbo Hotel room and social mechanics (primary))
- [primary] https://github.com/SimHacker/micropolis (Classic SimCity map and view presentation (historical/technical))
- [primary] https://www.djm.cc/simcity-2000-info.txt (Classic SimCity map and view presentation (historical/technical))
- [primary] https://sources.debian.org/src/micropolis-activity/0.0.20071228-7/manual/tutorial.html/ (Classic SimCity map and view presentation (historical/technical))
- [secondary] https://foldoc.org/SimCity+2000 (Classic SimCity map and view presentation (historical/technical))
- [primary] https://www.cs.cmu.edu/Groups/AI/util/areas/speech/systems/netaudio/contrib/simcity.txt (Classic SimCity map and view presentation (historical/technical))
- [primary] https://docs.rs/concinnity-core/latest/src/concinnity_core/components/voxel_world.rs.html (CPU isometric voxel rendering at scale (academic/implementation))
- [primary] https://docs.rs/roxlap-scene/0.4.2/src/roxlap_scene/billboard.rs.html (CPU isometric voxel rendering at scale (academic/implementation))
- [primary] https://github.com/pipliz/cpuvox (CPU isometric voxel rendering at scale (academic/implementation))
- [primary] https://docs.openttd.org/source/da/d7e/viewport_8cpp.html (CPU isometric voxel rendering at scale (academic/implementation))
- [primary] https://ratatui.rs/concepts/rendering/under-the-hood/ (CPU isometric voxel rendering at scale (academic/implementation))
- [blog] https://shaunlebron.github.io/IsometricBlocks (CPU isometric voxel rendering at scale (academic/implementation))
- [blog] https://icewyrmgames.github.io/research/scroll-back-the-theory-and-practice-of-cameras-in-side-scrollers (2D side view with depth lanes and follow camera (prior art))
- [primary] https://create.highrise.game/learn/studio-api/scripts/SideScrollerCamera (2D side view with depth lanes and follow camera (prior art))
- [secondary] https://en.wikipedia.org/wiki/Dungeon_Fighter_Online (2D side view with depth lanes and follow camera (prior art))
- [blog] https://tilebasedworlds-com.pages.dev/tutorial/a-side-scrolling-world/depth/ (2D side view with depth lanes and follow camera (prior art))
- [secondary] https://www.worthplaying.com/article/2016/8/18/reviews/100410-ps4-review-dead-island-retro-revenge/ (2D side view with depth lanes and follow camera (prior art))
- [blog] https://critpoints.net/2015/03/20/65/ (2D side view with depth lanes and follow camera (prior art))
- [primary] https://github.com/duckietm/Polaris-Emulator/blob/HEAD/Emulator/src/main/java/polaris/hotel/rooms/RoomCycleService.java (Habbo emulator server architecture and lightweight hosting (practitioner))
- [primary] https://github.com/duckietm/Polaris-Emulator/blob/HEAD/Emulator/src/main/java/polaris/hotel/rooms/RoomLifecycle.java (Habbo emulator server architecture and lightweight hosting (practitioner))
- [primary] https://github.com/Quackster/Kepler/blob/HEAD/Kepler-Server/src/main/java/org/alexdev/kepler/game/room/managers/RoomTaskManager.java (Habbo emulator server architecture and lightweight hosting (practitioner))
- [primary] https://github.com/Quackster/Kepler/blob/HEAD/Kepler-Server/src/main/java/org/alexdev/kepler/game/room/Room.java (Habbo emulator server architecture and lightweight hosting (practitioner))
