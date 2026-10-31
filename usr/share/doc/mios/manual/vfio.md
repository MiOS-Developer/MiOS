<!-- AI-hint: Manual pages distilled from the source comments of vfio, sanitized, each passage anchored to the comment it came from. -->

# vfio

### MiOS Looking Glass shared memory, domain profile and...

MiOS Looking Glass shared memory, domain profile and launcher.

Every libvirt domain that Looking Glass displays gets the same profile, projected from
the SSOT [vm.looking_glass] table (usr/share/mios/mios.toml):

* SPICE graphics on a fixed listen address/port (the client connects to the same
  spice_listen/spice_port, so the two cannot drift), plus a localhost VNC console on the
  emulated display for virt-manager / Cockpit when vnc_console is set;
* exactly one spicevmc virtio channel (com.redhat.spice.0) for clipboard;
* audio over SPICE (an <audio type="spice"> backend, an ich9 sound card);
* host-passthrough CPU with 1 socket x (vcpus / threads_per_core) cores x
  threads_per_core threads and the topoext feature (AMD SMT is otherwise invisible);
* <clock offset="localtime"> (Windows keeps the RTC in local time), timers untouched;
* the ivshmem-plain <shmem> framebuffer of shm_size_mb.

The same code renders the shipped templates (virt-apply.sh, xbox-repair.sh) and patches an
installed domain (--apply-domain dumps the inactive XML, patches it, validates it with
virt-xml-validate and redefines it; a running domain is left untouched).

Guest side: the client is built from the upstream default-branch HEAD at bake time
(automation/69-bake-lookingglass-client.sh records the commit in /usr/share/mios/looking-glass/build.json).
--provision-guest finds the signed upstream IDD build of that same commit on looking-glass.io and runs
usr/share/mios/windows/mios-looking-glass-idd.ps1 in the Windows guest over WinRM (SHA-256 and
Authenticode checked there; the legacy host service is removed). With win:setGuestRes the client then
asks the IDD for a guest mode equal to its window in physical pixels, so the guest renders 1:1.

<!-- mios-src:9c367ea2f5b8 from usr/libexec/mios/vfio/setup-looking-glass.py:4-30 -->

### graphics

-- graphics: one SPICE server on the fixed port --------------------------
Only the SSOT-owned fields are rewritten (type/port/autoport/listen and the <listen> child); every other
attribute (passwd, keymap, defaultMode, ...) and child (<image>, <streaming>, <gl>, ...) of the existing
SPICE / VNC server is carried over, so a re-apply never silently drops operator settings.

<!-- mios-src:71d1c5fe9bac from usr/libexec/mios/vfio/setup-looking-glass.py:269-272 -->

### The VNC console password from the root-only...

The VNC console password from the root-only [vm.looking_glass].vnc_passwd_file.

    Returns "" when the console or the password is switched off in the SSOT (the passwd is removed), None
    when the file cannot be read here (e.g. an unprivileged render: the domain's current passwd is kept),
    else the password. A missing file is generated (0600, 8 random characters) when `create` is set; a file
    readable by group/others is tightened to 0600; a symlink, a non-regular file or a foreign owner is refused.

<!-- mios-src:f80786560abf from usr/libexec/mios/vfio/setup-looking-glass.py:560-565 -->
