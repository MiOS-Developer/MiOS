<!-- AI-hint: Manual pages distilled from the source comments of game-clients, sanitized, each passage anchored to the comment it came from. -->

# game-clients

### An X11 client that behaves the way real game windows do...

An X11 client that behaves the way real game windows do under XWayland.

Recorded evidence (miosd placement journal, Forza Horizon 6 / Proton, class steam_app_2483190):
the window mapped at 1920x1080, then reconfigured itself to 3840x1080 -- the XWayland root, i.e. the union of
both outputs -- at the root origin. Wine marks a non-resizable (WS_POPUP) window fixed-size with
WM_NORMAL_HINTS min == max and re-issues those hints whenever its size changes, and positions itself with
ConfigureRequests in X root coordinates. This probe reproduces those requests on demand:

  --fixed            min == max size hints (Wine non-resizable / SDL_WINDOW_RESIZABLE off)
  --wine-hints       after every ConfigureNotify re-pin min == max to the new size (Wine semantics)
  --pos X,Y          USPosition request at map time (SDL/Wine CW_USEDEFAULT-less windows)
  --root-size        map at the root size at 0,0 (borderless "fullscreen" over every output)
  --remap-root MS    MS ms after mapping, ConfigureRequest to 0,0 at the root size (the Forza remap)
  --remap-to X,Y,WxH with --remap-root: request this box instead of the root (an oversize remap that is not the
                     root, or a game moving itself partly off-screen)
  --change-size WxH  --change-at MS after mapping: re-pin the hints to WxH (when fixed) and send a size-only
                     ConfigureRequest (a windowed game applying a new resolution from its settings menu)
  --transient        map a resizable parent window of the same class first and make this window
                     WM_TRANSIENT_FOR it (a Wine application's installer/file/settings dialog)
  --fullscreen       _NET_WM_STATE_FULLSCREEN at map time
  --borderless       _MOTIF_WM_HINTS decorations = 0
  --no-adapt         keep painting only the original size and set no window background (a GL/Vulkan game that
                     never recreates its swapchain): any area the compositor grows the window by shows black

It repaints its whole believed client area with --color on every Expose/ConfigureNotify, so a region that
stays black on screen is the compositor's, not the probe's. Each ConfigureNotify (real or synthetic) is a
JSON line on --log: {"t", "event", "x", "y", "w", "h", "send_event"}.

<!-- mios-src:d0635afbaec3 from tests/fixtures/game-clients/x11-game-probe.py:5-32 -->
