<!-- AI-hint: Manual pages distilled from the source comments of tmpfiles.d, sanitized, each passage anchored to the comment it came from. -->

# tmpfiles.d

### Build-output sentinel directory. The Forgejo Runner...

Build-output sentinel directory. The Forgejo Runner workflow writes
/var/lib/mios/forge-runner/last-build.txt after a successful
`podman build`; mios-bootc-switch.path watches this exact path on
the host side. The directory is shared between the runner container
and the host (Volume= bind in the Quadlet) so the watcher fires
regardless of which side wrote the file.

<!-- mios-src:a3a681b03e44 from usr/lib/tmpfiles.d/mios-forge-runner.conf:14-19 -->

### Admin-override directory for an optional app.ini and any...

Admin-override directory for an optional app.ini and any custom
templates / static asset overrides. Mounted into the container as
/etc/mios/forge:ro so admins can edit on the host without rebuilding
the image. Operator-edited; tracked under /etc/ per Law 1
(USR-OVER-ETC: /etc/ is admin-override only).

<!-- mios-src:49f51587ad0c from usr/lib/tmpfiles.d/mios-forge.conf:20-24 -->

### Vendor worker config copy-if-absent into the worker...

Vendor worker config copy-if-absent into the worker HERMES_HOME. Once present,
operator edits survive every boot. mios-hermes-firstboot NEVER touches this
path (it only re-thins /var/lib/mios/hermes/config.yaml), so the non-thin
worker config is durable. To restore vendor defaults: delete the file and
restart systemd-tmpfiles-setup.

<!-- mios-src:5b70a604c4ae from usr/lib/tmpfiles.d/mios-hermes-worker.conf:17-21 -->

### Hostname (LAW 4: /etc is Day-2 only)...

-- Hostname (LAW 4: /etc is Day-2 only) ------------------------------------
Copy the image-baked default to /etc/hostname on first boot only if absent.
mios-init overwrites it with the unique mios-XXXXX derived from machine-id.

<!-- mios-src:b8a2baba9704 from usr/lib/tmpfiles.d/mios-infra.conf:41-43 -->

### var/lib/mios/mcp ownership is declared authoritatively in...

/var/lib/mios/mcp ownership is declared authoritatively in mios.conf as
mios-ai:mios-ai (the service's User=). This file previously ALSO declared it
as mios:mios 0755, and the conflicting entry reverted the correct ownership on
every tmpfiles re-apply -> mcp-init.sh chmod EPERM -> mios-mcp crash-loop
(operator 2026-06-01). Removed the duplicate; mios.conf is the SoT.

<!-- mios-src:8fdd1edae862 from usr/lib/tmpfiles.d/mios-infra.conf:49-53 -->

### var/log/mios/mcp ownership

/var/log/mios/mcp ownership: see note above -- authoritative mios-ai:mios-ai
entry is in mios.conf; the duplicate mios:mios line here was removed (it
reverted ownership + crash-looped mios-mcp).

<!-- mios-src:af06eaac88be from usr/lib/tmpfiles.d/mios-infra.conf:58-60 -->

### WS-A4

WS-A4: OS-level GC backstop -- systemd-tmpfiles --clean ages out KV slot/fork
save files (mios-kv-*.bin) idle longer than 1d, so an unbounded fork fan-out
can't fill the disk even when the agent-pipe's in-process kv-gc isn't co-located
with this lane. An actively-paged conversation bumps its file's mtime each save,
so a live KV is never aged out; only stale/abandoned files are reclaimed.

<!-- mios-src:16d6fa749c5a from usr/lib/tmpfiles.d/mios-llamacpp.conf:11-15 -->

### opencode config dir (gateway reads OPENCODE_CONFIG here)....

opencode config dir (gateway reads OPENCODE_CONFIG here). The vendored
opencode.json SoT ships read-only at /usr/share/mios/opencode/opencode.json
(Law 1 USR-OVER-ETC); automation/72-hermes-agent.sh PHASE 2 copies it here
as the admin-override the gateway actually reads. This entry guarantees the
dir + mios-ai ownership for ProtectSystem=strict on every boot.

<!-- mios-src:bba9f2c36d1c from usr/lib/tmpfiles.d/mios-opencode-gateway.conf:11-15 -->

### NOTE (#57): the landed config FILE must be GROUP-readable...

NOTE (#57): the landed config FILE must be GROUP-readable by mios-ai (the
gateway user) per the MiOS cross-agent-read convention (chgrp mios-ai + 0640).
This is done by automation/72-hermes-agent.sh when it lands the file, NOT here:
systemd-tmpfiles refuses to chgrp/chmod a root-owned file that lives under a
mios-ai-owned dir ("unsafe path transition"), so a tmpfiles `z` line cannot do
it. Without the build-time chgrp the gateway gets "PermissionDenied:
FileSystem.readFile (/etc/mios/opencode/opencode.json)".

<!-- mios-src:2fd336135b13 from usr/lib/tmpfiles.d/mios-opencode-gateway.conf:17-23 -->

### Vendor settings.yml is shipped at...

Vendor settings.yml is shipped at /usr/share/mios/searxng/settings.yml
and copied into /etc/mios/searxng/ on first boot if no operator copy
exists. C= is "create-if-absent and copy from source" -- it never
overwrites an existing file, so a hand-edited settings.yml survives.

<!-- mios-src:15ad1de4fe14 from usr/lib/tmpfiles.d/mios-searxng.conf:17-20 -->

### Owned by the agent-pipe sysuser (uid 822) so the miner...

Owned by the agent-pipe sysuser (uid 822) so the miner timer (which
runs as that user) can write the catalog.json + any operator-
authored template files dropped here. Group=mios-ai (gid 850) so
every other AI agent (hermes, opencode, ...) can READ the
catalog without an explicit per-agent ACL. USER/SYSTEM/AI
separation -- writes still require uid 822 / sudo.

<!-- mios-src:b62d5b2e3de3 from usr/lib/tmpfiles.d/mios-skills.conf:17-22 -->

### Copy (NOT symlink) Bibata cursor + Geist fonts into the...

Copy (NOT symlink) Bibata cursor + Geist fonts into the operator's
xdg-data tree so flatpak sandboxes resolve them. Flatpak refuses to
expose /usr/* paths -- "Path /usr is reserved by Flatpak" -- so a
symlink under ~/.local/share/icons that POINTS at /usr/share/icons/
is unreadable from inside a flatpak sandbox (the sandbox follows the
symlink, hits /usr, gets EACCES, and the app falls back to the
default cursor theme). Operator 2026-05-11 (twice): "bibata cursor
is NOT global at all !!! still see broken cursor in epiphany and
other Linux windows". Copy operations bring the actual files into
xdg-data, which IS mapped into every flatpak sandbox.

`C` = "Copy if missing" -- recursively seeds the directory from the
source path on first boot; idempotent on subsequent boots. On image
rebuilds (bootc switch), /var is reset, so this re-runs cleanly.

<!-- mios-src:638f15bbbd1b from usr/lib/tmpfiles.d/mios-user.conf:34-47 -->

### Per-user default cursor inheritance. Apps that consult...

Per-user default cursor inheritance. Apps that consult ~/.icons/default
before /usr/share/icons/default (or that ignore /usr/share entirely
in a flatpak sandbox) see Bibata via this redirect. The actual
index.theme is shipped under /etc/skel/.icons/default/ so the
canonical `C /var/home/mios <- /etc/skel` line above seeds it on
first boot. Mirrors /usr/share/icons/default/index.theme.

<!-- mios-src:fe0efea57622 from usr/lib/tmpfiles.d/mios-user.conf:51-56 -->

### var/lib/mios/daemon -- the consolidated micro-LLM daemon's...

/var/lib/mios/daemon -- the consolidated micro-LLM daemon's state dir
(state.json + launch_failures.json). Owned by mios-ai (the daemon's User=
after the agent-user consolidation); 0755 so the OWUI mios_sidecar Filter --
a DIFFERENT uid -- can still traverse + read the world-readable state.json.
Without this line the dir was created root:root by the pre-consolidation
root-run daemon, then EPERM-crashed the mios-ai daemon's startup chmod +
state writes ("Operation not permitted: '/var/lib/mios/daemon'", 2026-05-24).

<!-- mios-src:5ea141d9ad1c from usr/lib/tmpfiles.d/mios.conf:10-16 -->

### var/lib/mios/mcp -- mios-mcp.service (Agent Context / MCP)...

/var/lib/mios/mcp -- mios-mcp.service (Agent Context / MCP) state dir
(state.db). Owned by mios-ai (the service's User=). SAME fix as daemon
above: pre-consolidation it was created mios:mios, so the mios-ai service
EPERM-crashed on mcp-init.sh's chmod ("cannot change permissions of
'/var/lib/mios/mcp': Operation not permitted", 33k restarts, 2026-05-27).

<!-- mios-src:a91500914799 from usr/lib/tmpfiles.d/mios.conf:18-22 -->

### var/lib/mios/ai/coderun -- per-call scratch workspaces for...

/var/lib/mios/ai/coderun -- per-call scratch workspaces for the `coderun`
verb's bubblewrap sandbox (mios-coderun). Each run makes + removes its own
cr-<ts>-<rand> subdir. GROUP-WRITABLE (0770): the `coderun` verb is dispatched
through the launcher BROKER, which runs as user `mios` (a member of the mios-ai
group), NOT as mios-ai itself -- so the broker must be able to create scratch
subdirs here. At 0750 the broker got EACCES ("scratch mkdir failed") and every
dispatched coderun (incl. the native-loop compute prefetch) failed; 0770 lets
any mios-ai-group member (broker + agent) create the per-call workspace. (2026-06-19)

<!-- mios-src:91f148a8d502 from usr/lib/tmpfiles.d/mios.conf:31-38 -->

### var/lib/mios/ai/dispatch -- WS-A13 per-dispatch sandbox...

/var/lib/mios/ai/dispatch -- WS-A13 per-dispatch sandbox workspaces. The
risk-tier sandbox (mios_sandbox) confines a write/interactive-tier verb to a
<verbhash>-<uuid> subdir here (rest of the fs read-only). 0770 like coderun
(the launcher broker runs as `mios`, a mios-ai-group member). Age-out cleans
abandoned workspaces. NO-MKDIR-IN-VAR (Law 2): declared, never built at image-time.

<!-- mios-src:e22c39fdb40b from usr/lib/tmpfiles.d/mios.conf:40-44 -->

### var/lib/mios/ai/artifacts -- the agent write_file / docgen...

/var/lib/mios/ai/artifacts -- the agent write_file / docgen output target. 2770
(group-writable + setgid) so the OPERATOR (mios, a mios-ai group member) running
the `hermes` CLI/REPL can write artifacts here too, not only the mios-ai services
-- a bare 0750 EACCES'd the REPL's write_file ("Permission denied:
/var/lib/mios/ai/artifacts/.hermes-tmp", operator 2026-06-16). setgid keeps new
files group=mios-ai so the agent-pipe / gateway still read them.

<!-- mios-src:837569963cb6 from usr/lib/tmpfiles.d/mios.conf:47-52 -->

### var/lib/mios/code-server -- dedicated workspace for the...

/var/lib/mios/code-server -- dedicated workspace for the
mios-code-server Quadlet. Owned by uid/gid 1000 (the container's
upstream 'coder' user); on a deployed MiOS host, mios is uid 1000
too so this is also the operator's effective home for the editor.
On podman-machine-os dev-VM (mios auto-allocated to 992) the
numeric owner stays 1000 so the container can write -- operator
can symlink /var/home/mios/git into /var/lib/mios/code-server to
project their host repos into the editor sidebar.

<!-- mios-src:08c8099618c6 from usr/lib/tmpfiles.d/mios.conf:54-61 -->

### Shared agent-env cache dir -- world-writable so the...

Shared agent-env cache dir -- world-writable so the operator-uid
AND the agent-uid (mios-agent-pipe) can both refresh inventory caches
(windows-apps.cache, windows-games.cache). Mode 0777 (NOT 1777/sticky):
both uids must atomically mv-REPLACE each other's cache files, but the
sticky bit blocks a non-owner from renaming-over a file it doesn't own,
so every cross-uid cache refresh failed with "mv ... Operation not
permitted" and broke app launches / inventory (operator 2026-05-20).
The cache is regenerable + non-sensitive; on this single-user host
there is no other local uid for the sticky bit to protect against.

<!-- mios-src:215fbe133435 from usr/lib/tmpfiles.d/mios.conf:71-79 -->

### OWUI bind-mounts these agent-output dirs :ro (for...

OWUI bind-mounts these agent-output dirs :ro (for suggestions/context). They
MUST exist at boot or the mios-open-webui container fails to start ("statfs
/var/lib/mios/<dir>: no such file or directory" -> 965x crash-loop, observed
on the fresh MiOS-DEV VM 2026-06-05). Owned by mios-ai (the producing services
hermes-tail / delegation-prefilter / log-watcher / agent-nudger all run as
mios-ai); 0755 so OWUI (uid 817) can read them through the read-only mount.

<!-- mios-src:de2119c75df5 from usr/lib/tmpfiles.d/mios.conf:81-86 -->

### task_3176 war-room activity sink. Two DIFFERENT identities must...

task_3176 war-room activity sink. Two DIFFERENT identities must share this file:
  WRITER = the mios-agents container's war-room (mios-a2o), which runs as the
           `coder` user = host uid 1000 (== the `mios` operator; 1:1 under rootful
           podman) and appends JSONL + writes sibling .tmp files during trim.
  READER = mios-agent-pipe (mios-ai), which tails the file into the reasoning
           channel. mios-ai and the operator both belong to the mios-ai group.
So: owner uid 1000 (writer can create/append/rename freely in a dir it owns),
group mios-ai + setgid (02775) so every appended line inherits the mios-ai group
the reader reads through -- no reliance on umask/world bits. Only populated when
[frontier].stream_to_reasoning is on; empty otherwise (OFF-by-default preserved).

<!-- mios-src:77da582bd347 from usr/lib/tmpfiles.d/mios.conf:88-97 -->
### Looking Glass IVSHMEM framebuffer

Looking Glass IVSHMEM framebuffer: libvirt <shmem name="looking-glass"> (usr/libexec/mios/Xbox-Final-NoAutoSelect.xml),
mios.toml [vm.looking_glass] shm_path. QEMU runs as svirt_t and may only map it as svirt_tmpfs_t:
the fcontext comes from automation/38-selinux.sh (build) and setup-looking-glass.py --ensure-selinux
(mios-vfio-setup.service); `f` creates it with that default label and `z` restores the label and
mode on a file that already existed with plain tmpfs_t (QEMU then fails with EACCES).

<!-- mios-src:752f479ad80b from usr/lib/tmpfiles.d/50-mios.conf:211-215 -->

### swtpm_setup (run as libvirt's swtpm_user tss) writes the...

swtpm_setup (run as libvirt's swtpm_user tss) writes the local CA here for every NEW vTPM; swtpm-tools ships it
tss:root 0750. Forcing root:root 0755 made swtpm_setup fail "Need read/write rights on statedir ... for user tss"
for any newly created vTPM domain (MiOS-Xbox debug VM, 2026-10-23). Existing TPM state is not touched.

<!-- mios-src:1c4d3dfa9acf from usr/lib/tmpfiles.d/50-mios.conf:257-259 -->
