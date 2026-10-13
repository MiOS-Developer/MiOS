<!-- AI-hint: Derived reference documentation for systemd unit files shipped with MiOS. -->

# MiOS Systemd Units

This document is derived directly from the systemd unit files in the repository.

<!-- MIOS-GEN:units -->
| Unit | Directory | Description |
|---|---|---|
| `ModemManager.service` | `usr/lib/systemd/system` | Modem Manager |
| `NetworkManager-dispatcher.service` | `usr/lib/systemd/system` | Network Manager Script Dispatcher Service |
| `NetworkManager-wait-online.service` | `usr/lib/systemd/system` | Network Manager Wait Online |
| `NetworkManager.service` | `usr/lib/systemd/system` | Network Manager |
| `abrt-journal-core.service` | `usr/lib/systemd/system` | ABRT coredumpctl message creator |
| `abrt-oops.service` | `usr/lib/systemd/system` | ABRT kernel log watcher |
| `abrt-pstoreoops.service` | `usr/lib/systemd/system` | ABRT kernel oops pstore collector |
| `abrt-vmcore.service` | `usr/lib/systemd/system` | ABRT kernel panic detection |
| `abrt-xorg.service` | `usr/lib/systemd/system` | ABRT Xorg log watcher |
| `abrtd.service` | `usr/lib/systemd/system` | ABRT Daemon |
| `accounts-daemon.service` | `usr/lib/systemd/system` | Accounts Service |
| `arp-ethers.service` | `usr/lib/systemd/system` | Load static arp entries |
| `at-spi-dbus-bus.service` | `usr/lib/systemd/user` | Accessibility services bus |
| `atd.service` | `usr/lib/systemd/system` | Deferred execution scheduler |
| `audit-rules.service` | `usr/lib/systemd/system` | Load Audit Rules |
| `auditd.service` | `usr/lib/systemd/system` | Security Audit Logging Service |
| `auth-rpcgss-module.service` | `usr/lib/systemd/system` | Kernel Module supporting RPCSEC_GSS |
| `authselect-apply-changes.service` | `usr/lib/systemd/system` | Apply authselect changes |
| `autofs.service` | `usr/lib/systemd/system` | Automounts filesystems on demand |
| `autovt@.service` | `usr/lib/systemd/system` | Getty on %I |
| `avahi-daemon.service` | `usr/lib/systemd/system` | Avahi mDNS/DNS-SD Stack |
| `avahi-daemon.socket` | `usr/lib/systemd/system` | Avahi mDNS/DNS-SD Stack Activation Socket |
| `basic.target` | `usr/lib/systemd/system` | Basic System |
| `basic.target` | `usr/lib/systemd/user` | Basic System |
| `blk-availability.service` | `usr/lib/systemd/system` | Availability of block devices |
| `blockdev@.target` | `usr/lib/systemd/system` | Block Device Preparation for %f |
| `bluetooth.service` | `usr/lib/systemd/system` | Bluetooth service |
| `bluetooth.target` | `usr/lib/systemd/system` | Bluetooth Support |
| `bluetooth.target` | `usr/lib/systemd/user` | Bluetooth |
| `bolt.service` | `usr/lib/systemd/system` | Thunderbolt system service |
| `boot-complete.target` | `usr/lib/systemd/system` | Boot Completion Check |
| `bootc-destructive-cleanup.service` | `usr/lib/systemd/system` | Cleanup previous the installation after an alongside installation |
| `bootc-fetch-apply-updates.service` | `usr/lib/systemd/system` | Apply bootc updates |
| `bootc-fetch-apply-updates.timer` | `usr/lib/systemd/system` | Apply bootc updates |
| `bootc-finalize-staged.service` | `usr/lib/systemd/system` | Composefs Finalize Staged Deployment |
| `bootc-publish-rhsm-facts.service` | `usr/lib/systemd/system` | Publish bootc facts to Red Hat Subscription Manager |
| `bootc-root-setup.service` | `usr/lib/systemd/system` | bootc setup root |
| `bootc-status-updated-onboot.target` | `usr/lib/systemd/system` | Bootc status trigger state sync |
| `bootc-status-updated.path` | `usr/lib/systemd/system` | Monitor bootc for status changes |
| `bootc-status-updated.target` | `usr/lib/systemd/system` | Target for bootc status changes |
| `bootc-sysusers-shadow-sync.service` | `usr/lib/systemd/system` | Sync /etc/shadow and /etc/gshadow before systemd-sysusers |
| `booth-arbitrator.service` | `usr/lib/systemd/system` | Booth - Ticket Manager for Pacemaker Clusters |
| `booth@.service` | `usr/lib/systemd/system` | Booth - Ticket Manager for Pacemaker Clusters |
| `bootloader-update.service` | `usr/lib/systemd/system` | Update bootloader on boot |
| `breakpoint-pre-basic.service` | `usr/lib/systemd/system` | Breakpoint Before basic.target |
| `breakpoint-pre-mount.service` | `usr/lib/systemd/system` | Breakpoint Before Mounting the Root Filesystem on /sysroot/ |
| `breakpoint-pre-switch-root.service` | `usr/lib/systemd/system` | Breakpoint Before Switching Root |
| `breakpoint-pre-udev.service` | `usr/lib/systemd/system` | Breakpoint Before Starting to Process Kernel uevents |
| `calls-daemon.service` | `usr/lib/systemd/user` | A phone dialer and call handler (daemon mode) |
| `canberra-system-bootup.service` | `usr/lib/systemd/system` | Play Bootup Sound |
| `canberra-system-shutdown-reboot.service` | `usr/lib/systemd/system` | Play Reboot Sound |
| `canberra-system-shutdown.service` | `usr/lib/systemd/system` | Play Shutdown Sound |
| `capsule@.service` | `usr/lib/systemd/system` | Capsule Service Manager for %i |
| `capsule@.target` | `usr/lib/systemd/user` | Main Capsule Target for %i |
| `ceph-crash.service` | `usr/lib/systemd/system` | Ceph crash dump collector |
| `ceph-fuse.target` | `usr/lib/systemd/system` | ceph target allowing to start/stop all ceph-fuse@.service instances at once |
| `ceph-fuse@.service` | `usr/lib/systemd/system` | Ceph FUSE client |
| `ceph.target` | `usr/lib/systemd/system` | ceph target allowing to start/stop all ceph*@.service instances at once |
| `certmonger.service` | `usr/lib/systemd/system` | Certificate monitoring and PKI enrollment |
| `chrony-wait.service` | `usr/lib/systemd/system` | Wait for chrony to synchronize system clock |
| `chronyd-restricted.service` | `usr/lib/systemd/system` | NTP client (restricted) |
| `chronyd.service` | `usr/lib/systemd/system` | NTP client/server |
| `cloud-config.service` | `usr/lib/systemd/system` | Cloud-init: Config Stage |
| `cloud-config.target` | `usr/lib/systemd/system` | Cloud-config availability |
| `cloud-final.service` | `usr/lib/systemd/system` | Cloud-init: Final Stage |
| `cloud-init-hotplugd.service` | `usr/lib/systemd/system` | Cloud-init: Hotplug Hook |
| `cloud-init-hotplugd.socket` | `usr/lib/systemd/system` | cloud-init hotplug hook socket |
| `cloud-init-local.service` | `usr/lib/systemd/system` | Cloud-init: Local Stage (pre-network) |
| `cloud-init-main.service` | `usr/lib/systemd/system` | Cloud-init: Single Process |
| `cloud-init-network.service` | `usr/lib/systemd/system` | Cloud-init: Network Stage |
| `cloud-init.target` | `usr/lib/systemd/system` | Cloud-init target |
| `cockpit-issue.service` | `usr/lib/systemd/system` | Cockpit issue updater service |
| `cockpit-session-socket-user.service` | `usr/lib/systemd/system` | Dynamic user for /run/cockpit/session socket |
| `cockpit-session.socket` | `usr/lib/systemd/system` | Initiator socket for Cockpit sessions |
| `cockpit-session@.service` | `usr/lib/systemd/system` | Cockpit session %I |
| `cockpit-wsinstance-http.service` | `usr/lib/systemd/system` | Cockpit Web Service http instance |
| `cockpit-wsinstance-http.socket` | `usr/lib/systemd/system` | Socket for Cockpit Web Service http instance |
| `cockpit-wsinstance-https-factory.socket` | `usr/lib/systemd/system` | Socket for Cockpit Web Service https instance factory |
| `cockpit-wsinstance-https-factory@.service` | `usr/lib/systemd/system` | Cockpit Web Service https instance factory |
| `cockpit-wsinstance-https@.service` | `usr/lib/systemd/system` | Cockpit Web Service https instance %I |
| `cockpit-wsinstance-https@.socket` | `usr/lib/systemd/system` | Socket for Cockpit Web Service https instance %I |
| `cockpit-wsinstance-socket-user.service` | `usr/lib/systemd/system` | Dynamic user for /run/cockpit/wsinstance/ sockets |
| `cockpit.service` | `usr/lib/systemd/system` | Cockpit Web Service |
| `cockpit.socket` | `usr/lib/systemd/system` | Cockpit Web Service Socket |
| `code-server.service` | `usr/lib/systemd/user` | code-server |
| `code-server@.service` | `usr/lib/systemd/system` | code-server |
| `colord-session.service` | `usr/lib/systemd/user` | Color management helper |
| `colord.service` | `usr/lib/systemd/system` | Manage, Install and Generate Color Profiles |
| `console-getty.service` | `usr/lib/systemd/system` | Console Getty |
| `container-getty@.service` | `usr/lib/systemd/system` | Container Getty on /dev/pts/%I |
| `containerd.service` | `usr/lib/systemd/system` | containerd container runtime |
| `corosync-notifyd.service` | `usr/lib/systemd/system` | Corosync Dbus and snmp notifier |
| `corosync-qdevice.service` | `usr/lib/systemd/system` | Corosync Qdevice daemon |
| `corosync-qnetd.service` | `usr/lib/systemd/system` | Corosync Qdevice Network daemon |
| `corosync.service` | `usr/lib/systemd/system` | Corosync Cluster Engine |
| `cpupower.service` | `usr/lib/systemd/system` | Apply cpupower configuration |
| `crm_mon.service` | `usr/lib/systemd/system` | Daemon for pacemaker monitor |
| `crond.service` | `usr/lib/systemd/system` | Command Scheduler |
| `cryptsetup-pre.target` | `usr/lib/systemd/system` | Local Encrypted Volumes (Pre) |
| `cryptsetup.target` | `usr/lib/systemd/system` | Local Encrypted Volumes |
| `ctrl-alt-del.target` | `usr/lib/systemd/system` | System Reboot |
| `dbus-broker.service` | `usr/lib/systemd/system` | D-Bus System Message Bus |
| `dbus-broker.service` | `usr/lib/systemd/user` | D-Bus User Message Bus |
| `dbus-daemon.service` | `usr/lib/systemd/system` | D-Bus System Message Bus |
| `dbus-daemon.service` | `usr/lib/systemd/user` | D-Bus User Message Bus |
| `dbus-org.bluez.obex.service` | `usr/lib/systemd/user` | Bluetooth OBEX service |
| `dbus-org.freedesktop.hostname1.service` | `usr/lib/systemd/system` | Hostname Service |
| `dbus-org.freedesktop.import1.service` | `usr/lib/systemd/system` | Disk Image Download Service |
| `dbus-org.freedesktop.import1.service` | `usr/lib/systemd/user` | Disk Image Download Service |
| `dbus-org.freedesktop.locale1.service` | `usr/lib/systemd/system` | Locale Service |
| `dbus-org.freedesktop.login1.service` | `usr/lib/systemd/system` | User Login Management |
| `dbus-org.freedesktop.machine1.service` | `usr/lib/systemd/system` | Virtual Machine and Container Registration Service |
| `dbus-org.freedesktop.machine1.service` | `usr/lib/systemd/user` | Virtual Machine and Container Registration Service |
| `dbus-org.freedesktop.portable1.service` | `usr/lib/systemd/system` | Portable Service Manager |
| `dbus-org.freedesktop.sysupdate1.service` | `usr/lib/systemd/system` | System Update Service |
| `dbus-org.freedesktop.timedate1.service` | `usr/lib/systemd/system` | Time & Date Service |
| `dbus.socket` | `usr/lib/systemd/system` | D-Bus System Message Bus Socket |
| `dbus.socket` | `usr/lib/systemd/user` | D-Bus User Message Bus Socket |
| `dconf.service` | `usr/lib/systemd/user` | User preferences database |
| `debug-shell.service` | `usr/lib/systemd/system` | Early root shell on /dev/tty9 FOR DEBUGGING ONLY |
| `default.target` | `usr/lib/systemd/system` | Graphical Interface |
| `default.target` | `usr/lib/systemd/user` | Main User Target |
| `dev-binderfs.mount` | `usr/lib/systemd/system` | Mount binderfs partition |
| `dev-hugepages.mount` | `usr/lib/systemd/system` | Huge Pages File System |
| `dev-mqueue.mount` | `usr/lib/systemd/system` | POSIX Message Queue File System |
| `dhcpcd.service` | `usr/lib/systemd/system` | A minimalistic network configuration daemon with DHCPv4, rdisc and DHCPv6 support |
| `dhcpcd@.service` | `usr/lib/systemd/system` | dhcpcd on %I |
| `dirmngr.service` | `usr/lib/systemd/user` | GnuPG network certificate management daemon |
| `dirmngr.socket` | `usr/lib/systemd/user` | GnuPG network certificate management daemon |
| `dlm.service` | `usr/lib/systemd/system` | dlm control daemon |
| `dm-event.service` | `usr/lib/systemd/system` | Device-mapper event daemon |
| `dm-event.socket` | `usr/lib/systemd/system` | Device-mapper event daemon FIFOs |
| `dnf-makecache.service` | `usr/lib/systemd/system` | dnf5 makecache |
| `dnf-makecache.timer` | `usr/lib/systemd/system` | dnf5 makecache |
| `dnf5-makecache.service` | `usr/lib/systemd/system` | dnf5 makecache |
| `dnf5-makecache.timer` | `usr/lib/systemd/system` | dnf5 makecache |
| `dnf5-offline-transaction-cleanup.service` | `usr/lib/systemd/system` | Offline upgrade/transaction using DNF 5 failed |
| `dnf5-offline-transaction.service` | `usr/lib/systemd/system` | Offline upgrades/transactions using DNF 5 |
| `dnf5daemon-server.service` | `usr/lib/systemd/system` | DNF daemon |
| `dnsmasq.service` | `usr/lib/systemd/system` | DNS caching server. |
| `docker.service` | `usr/lib/systemd/system` | Docker Application Container Engine |
| `docker.socket` | `usr/lib/systemd/system` | Docker Socket for the API |
| `dracut-cmdline.service` | `usr/lib/systemd/system` | dracut cmdline hook |
| `dracut-initqueue.service` | `usr/lib/systemd/system` | dracut initqueue hook |
| `dracut-mount.service` | `usr/lib/systemd/system` | dracut mount hook |
| `dracut-pre-mount.service` | `usr/lib/systemd/system` | dracut pre-mount hook |
| `dracut-pre-pivot.service` | `usr/lib/systemd/system` | dracut pre-pivot and cleanup hook |
| `dracut-pre-trigger.service` | `usr/lib/systemd/system` | dracut pre-trigger hook |
| `dracut-pre-udev.service` | `usr/lib/systemd/system` | dracut pre-udev hook |
| `dracut-shutdown-onfailure.service` | `usr/lib/systemd/system` | Service executing upon dracut-shutdown failure to perform cleanup |
| `dracut-shutdown.service` | `usr/lib/systemd/system` | Restore /run/initramfs on shutdown |
| `driverctl@.service` | `usr/lib/systemd/system` | Load the driverctl override for %i |
| `emergency.service` | `usr/lib/systemd/system` | Emergency Shell |
| `emergency.target` | `usr/lib/systemd/system` | Emergency Mode |
| `evolution-addressbook-factory.service` | `usr/lib/systemd/user` | Evolution address book service |
| `evolution-alarm-notify.service` | `usr/lib/systemd/user` | Event and Task Reminders |
| `evolution-calendar-factory.service` | `usr/lib/systemd/user` | Evolution calendar service |
| `evolution-source-registry.service` | `usr/lib/systemd/user` | Evolution source registry |
| `evolution-user-prompter.service` | `usr/lib/systemd/user` | Evolution user prompter |
| `exit.target` | `usr/lib/systemd/system` | Exit the Container |
| `exit.target` | `usr/lib/systemd/user` | Exit the Session |
| `factory-reset-now.target` | `usr/lib/systemd/system` | Factory Reset Execution |
| `factory-reset.target` | `usr/lib/systemd/system` | Factory Reset Initiation |
| `fancontrol.service` | `usr/lib/systemd/system` | Start fan control, if configured |
| `fapolicyd.service` | `usr/lib/systemd/system` | File Access Policy Daemon |
| `fbd-alert-slider.service` | `usr/lib/systemd/user` | Feedbackd Alert Slider Handler |
| `filter-chain.service` | `usr/lib/systemd/user` | PipeWire filter chain daemon |
| `final.target` | `usr/lib/systemd/system` | Late Shutdown Services |
| `fips-crypto-policy-overlay.service` | `usr/lib/systemd/system` | Bind-mount FIPS crypto-policy in FIPS mode |
| `firewalld.service` | `usr/lib/systemd/system` | firewalld - dynamic firewall daemon |
| `first-boot-complete.target` | `usr/lib/systemd/system` | First Boot Complete |
| `flatpak-add-fedora-repos.service` | `usr/lib/systemd/system` | Add Fedora flatpak repositories |
| `flatpak-oci-authenticator.service` | `usr/lib/systemd/user` | flatpak oci authenticator |
| `flatpak-portal.service` | `usr/lib/systemd/user` | flatpak portal |
| `flatpak-session-helper.service` | `usr/lib/systemd/user` | flatpak session helper |
| `flatpak-system-helper.service` | `usr/lib/systemd/system` | flatpak system helper |
| `fprintd.service` | `usr/lib/systemd/system` | Fingerprint Authentication Daemon |
| `fsidd.service` | `usr/lib/systemd/system` | NFS FSID Daemon |
| `fstrim.service` | `usr/lib/systemd/system` | Discard unused blocks on filesystems from /etc/fstab |
| `fstrim.timer` | `usr/lib/systemd/system` | Discard unused filesystem blocks once a week |
| `fwupd-refresh.service` | `usr/lib/systemd/system` | Refresh fwupd metadata and update motd |
| `fwupd-refresh.timer` | `usr/lib/systemd/system` | Refresh fwupd metadata regularly |
| `fwupd.service` | `usr/lib/systemd/system` | Firmware update daemon |
| `gamemoded.service` | `usr/lib/systemd/user` | gamemoded |
| `gcr-ssh-agent.service` | `usr/lib/systemd/user` | GCR ssh-agent wrapper |
| `gcr-ssh-agent.socket` | `usr/lib/systemd/user` | GCR ssh-agent wrapper |
| `gdm.service` | `usr/lib/systemd/system` | GNOME Display Manager |
| `geoclue.service` | `usr/lib/systemd/system` | Location Lookup Service |
| `getty-pre.target` | `usr/lib/systemd/system` | Preparation for Logins |
| `getty.target` | `usr/lib/systemd/system` | Login Prompts |
| `getty@.service` | `usr/lib/systemd/system` | Getty on %I |
| `glib-pacrunner.service` | `usr/lib/systemd/user` | GLib proxy auto-configuration service |
| `gnome-headless-session@.service` | `usr/lib/systemd/system` | Headless desktop session |
| `gnome-keyring-daemon.service` | `usr/lib/systemd/user` | GNOME Keyring daemon |
| `gnome-keyring-daemon.socket` | `usr/lib/systemd/user` | GNOME Keyring daemon |
| `gnome-remote-desktop-configuration.service` | `usr/lib/systemd/system` | Configure GNOME Remote Desktop |
| `gnome-remote-desktop-handover.service` | `usr/lib/systemd/user` | GNOME Remote Desktop Handover |
| `gnome-remote-desktop-headless.service` | `usr/lib/systemd/user` | GNOME Remote Desktop (headless) |
| `gnome-remote-desktop.service` | `usr/lib/systemd/system` | GNOME Remote Desktop |
| `gnome-remote-desktop.service` | `usr/lib/systemd/user` | GNOME Remote Desktop |
| `gnome-session-basic-services.target` | `usr/lib/systemd/user` | GNOME basic session services |
| `gnome-session-initialized.target` | `usr/lib/systemd/user` | GNOME Session is initialized |
| `gnome-session-manager.target` | `usr/lib/systemd/user` | GNOME Session Manager is ready |
| `gnome-session-manager@.service` | `usr/lib/systemd/user` | GNOME Session Manager (session: %i) |
| `gnome-session-monitor.service` | `usr/lib/systemd/user` | Monitor Session leader for GNOME Session |
| `gnome-session-pre.target` | `usr/lib/systemd/user` | Tasks to be run before GNOME Session starts |
| `gnome-session-restart-dbus.service` | `usr/lib/systemd/user` | Restart DBus after GNOME Session shutdown |
| `gnome-session-services.target` | `usr/lib/systemd/user` | GNOME session services |
| `gnome-session-shutdown.target` | `usr/lib/systemd/user` | Shutdown running GNOME Session |
| `gnome-session-signal-init.service` | `usr/lib/systemd/user` | Signal initialization done to GNOME Session Manager |
| `gnome-session-x11-services-ready.target` | `usr/lib/systemd/user` | GNOME session X11 services |
| `gnome-session-x11-services.target` | `usr/lib/systemd/user` | GNOME session X11 services |
| `gnome-session.target` | `usr/lib/systemd/user` | GNOME Session |
| `gnome-session@.target` | `usr/lib/systemd/user` | GNOME Session (session: %i) |
| `gnome-software.service` | `usr/lib/systemd/user` | GNOME Software |
| `gpg-agent-browser.socket` | `usr/lib/systemd/user` | GnuPG cryptographic agent and passphrase cache (access for web browsers) |
| `gpg-agent-extra.socket` | `usr/lib/systemd/user` | GnuPG cryptographic agent and passphrase cache (restricted) |
| `gpg-agent-ssh.socket` | `usr/lib/systemd/user` | GnuPG cryptographic agent (ssh-agent emulation) |
| `gpg-agent.service` | `usr/lib/systemd/user` | GnuPG cryptographic agent and passphrase cache |
| `gpg-agent.socket` | `usr/lib/systemd/user` | GnuPG cryptographic agent and passphrase cache |
| `graphical-session-pre.target` | `usr/lib/systemd/user` | Session services which should run early before the graphical session is brought up |
| `graphical-session.target` | `usr/lib/systemd/user` | Current graphical user session |
| `graphical.target` | `usr/lib/systemd/system` | Graphical Interface |
| `greenboot-healthcheck.service` | `usr/lib/systemd/system` | Greenboot Health Checks Runner |
| `greenboot-set-rollback-trigger.service` | `usr/lib/systemd/system` | Greenboot Rollback trigger |
| `greenboot-success.target` | `usr/lib/systemd/system` | GreenBoot Healthcheck Success Target |
| `grub-boot-indeterminate.service` | `usr/lib/systemd/system` | Mark boot as indeterminate |
| `grub-boot-success.service` | `usr/lib/systemd/user` | Mark boot as successful |
| `grub-boot-success.timer` | `usr/lib/systemd/user` | Mark boot as successful after the user session has run 2 minutes |
| `grub2-systemd-integration.service` | `usr/lib/systemd/system` | Grub2 systemctl reboot --boot-loader-menu=... support |
| `gssproxy.service` | `usr/lib/systemd/system` | GSSAPI Proxy Daemon |
| `gssuserproxy.service` | `usr/lib/systemd/user` | GSS User Proxy |
| `gssuserproxy.socket` | `usr/lib/systemd/user` | GSS User Proxy |
| `gvfs-daemon.service` | `usr/lib/systemd/user` | Virtual filesystem service |
| `gvfs-goa-volume-monitor.service` | `usr/lib/systemd/user` | Virtual filesystem service - GNOME Online Accounts monitor |
| `gvfs-metadata.service` | `usr/lib/systemd/user` | Virtual filesystem metadata service |
| `gvfs-mtp-volume-monitor.service` | `usr/lib/systemd/user` | Virtual filesystem service - Media Transfer Protocol monitor |
| `gvfs-udisks2-volume-monitor.service` | `usr/lib/systemd/user` | Virtual filesystem service - disk device monitor |
| `halt.target` | `usr/lib/systemd/system` | System Halt |
| `hermes-dashboard.service` | `usr/lib/systemd/system` | MiOS' Hermes-Agent Dashboard (kanban board + chat + skills UI) |
| `hermes-worker-firstboot.service` | `usr/lib/systemd/system` | MiOS' Hermes-Worker first-boot config seed (:8643 non-thin worker) |
| `hermes-worker.path` | `usr/lib/systemd/system` | MiOS' watch for the Hermes venv -> (re)start hermes-worker |
| `hermes-worker.service` | `usr/lib/systemd/system` | MiOS' Hermes gateway (native tool loop, port key `hermes`) |
| `hibernate.target` | `usr/lib/systemd/system` | System Hibernation |
| `hostapd.service` | `usr/lib/systemd/system` | Hostapd IEEE 802.11 AP, IEEE 802.1X/WPA/WPA2/EAP/RADIUS Authenticator |
| `hybrid-sleep.target` | `usr/lib/systemd/system` | Hybrid Suspend+Hibernate |
| `hypervfcopyd.service` | `usr/lib/systemd/system` | Hyper-V FCOPY daemon |
| `hypervkvpd.service` | `usr/lib/systemd/system` | Hyper-V KVP daemon |
| `hypervvssd.service` | `usr/lib/systemd/system` | Hyper-V VSS daemon |
| `iio-sensor-proxy.service` | `usr/lib/systemd/system` | IIO Sensor Proxy service |
| `imports-pre.target` | `usr/lib/systemd/system` | Image Downloads (Pre) |
| `imports.target` | `usr/lib/systemd/system` | Image Downloads |
| `initrd-cleanup.service` | `usr/lib/systemd/system` | Cleaning Up and Shutting Down Daemons |
| `initrd-fs.target` | `usr/lib/systemd/system` | Initrd File Systems |
| `initrd-parse-etc.service` | `usr/lib/systemd/system` | Mountpoints Configured in the Real Root |
| `initrd-root-device.target` | `usr/lib/systemd/system` | Initrd Root Device |
| `initrd-root-fs.target` | `usr/lib/systemd/system` | Initrd Root File System |
| `initrd-switch-root.service` | `usr/lib/systemd/system` | Switch Root |
| `initrd-switch-root.target` | `usr/lib/systemd/system` | Switch Root |
| `initrd-udevadm-cleanup-db.service` | `usr/lib/systemd/system` | Cleanup udev Database |
| `initrd-usr-fs.target` | `usr/lib/systemd/system` | Initrd /usr File System |
| `initrd.target` | `usr/lib/systemd/system` | Initrd Default Target |
| `integritysetup-pre.target` | `usr/lib/systemd/system` | Local Integrity Protected Volumes (Pre) |
| `integritysetup.target` | `usr/lib/systemd/system` | Local Integrity Protected Volumes |
| `irqbalance.service` | `usr/lib/systemd/system` | irqbalance daemon |
| `iscsi-init.service` | `usr/lib/systemd/system` | One time configuration for iscsi.service |
| `iscsi-onboot.service` | `usr/lib/systemd/system` | Special handling of early boot iSCSI sessions |
| `iscsi-shutdown.service` | `usr/lib/systemd/system` | Logout off all iSCSI sessions on shutdown |
| `iscsi-starter.service` | `usr/lib/systemd/system` | ? |
| `iscsi.service` | `usr/lib/systemd/system` | Login and scanning of iSCSI devices |
| `iscsid.service` | `usr/lib/systemd/system` | Open-iSCSI |
| `iscsid.socket` | `usr/lib/systemd/system` | Open-iSCSI iscsid Socket |
| `iscsiuio.service` | `usr/lib/systemd/system` | iSCSI UserSpace I/O driver |
| `iscsiuio.socket` | `usr/lib/systemd/system` | Open-iSCSI iscsiuio Socket |
| `k3s.service` | `usr/lib/systemd/system` | Lightweight Kubernetes (K3s) |
| `kdump.service` | `usr/lib/systemd/system` | Crash recovery kernel arming |
| `kexec.target` | `usr/lib/systemd/system` | Reboot via kexec |
| `keyboxd.service` | `usr/lib/systemd/user` | GnuPG public key management service |
| `keyboxd.socket` | `usr/lib/systemd/user` | GnuPG public key management service |
| `keydb-sentinel.service` | `usr/lib/systemd/system` | KeyDB Sentinel |
| `keydb.service` | `usr/lib/systemd/system` | KeyDB persistent key-value database |
| `kmod-static-nodes.service` | `usr/lib/systemd/system` | Create List of Static Device Nodes |
| `kvm_stat.service` | `usr/lib/systemd/system` | Service that logs KVM kernel module trace events |
| `lastlog2-import.service` | `usr/lib/systemd/system` | Import lastlog data into lastlog2 database |
| `ldconfig.service` | `usr/lib/systemd/system` | Rebuild Dynamic Linker Cache |
| `libvirt-dbus.service` | `usr/lib/systemd/system` | Libvirt DBus Service |
| `libvirt-dbus.service` | `usr/lib/systemd/user` | Libvirt DBus Service |
| `libvirt-guests.service` | `usr/lib/systemd/system` | libvirt guests suspend/resume service |
| `libvirtd-admin.socket` | `usr/lib/systemd/system` | libvirt legacy monolithic daemon admin socket |
| `libvirtd-ro.socket` | `usr/lib/systemd/system` | libvirt legacy monolithic daemon read-only socket |
| `libvirtd-tcp.socket` | `usr/lib/systemd/system` | libvirt legacy monolithic daemon non-TLS IP socket |
| `libvirtd-tls.socket` | `usr/lib/systemd/system` | libvirt legacy monolithic daemon TLS IP socket |
| `libvirtd.service` | `usr/lib/systemd/system` | libvirt legacy monolithic daemon |
| `libvirtd.socket` | `usr/lib/systemd/system` | libvirt legacy monolithic daemon socket |
| `llama-rpc-server.service` | `usr/lib/systemd/system` | MiOS' llama.cpp RPC Server (Headless Blade Compute Node) |
| `lm_sensors.service` | `usr/lib/systemd/system` | Hardware Monitoring Sensors |
| `local-fs-pre.target` | `usr/lib/systemd/system` | Preparation for Local File Systems |
| `local-fs.target` | `usr/lib/systemd/system` | Local File Systems |
| `localsearch-3.service` | `usr/lib/systemd/user` | LocalSearch indexer |
| `localsearch-control-3.service` | `usr/lib/systemd/user` | Tracker Filesystem Indexing Proxy |
| `localsearch-writeback-3.service` | `usr/lib/systemd/user` | Tracker data writeback service |
| `logrotate.service` | `usr/lib/systemd/system` | Rotate log files |
| `logrotate.timer` | `usr/lib/systemd/system` | Daily rotation of log files |
| `lvm-devices-import.path` | `usr/lib/systemd/system` | lvm-devices-import to create system.devices |
| `lvm-devices-import.service` | `usr/lib/systemd/system` | Create lvm system.devices |
| `lvm2-lvmpolld.service` | `usr/lib/systemd/system` | LVM2 poll daemon |
| `lvm2-lvmpolld.socket` | `usr/lib/systemd/system` | LVM2 poll daemon socket |
| `lvm2-monitor.service` | `usr/lib/systemd/system` | Monitoring of LVM2 mirrors, snapshots etc. using dmeventd or progress polling |
| `lxc-monitord.service` | `usr/lib/systemd/system` | LXC Container Monitoring Daemon |
| `lxc-net.service` | `usr/lib/systemd/system` | LXC network bridge setup |
| `lxc.service` | `usr/lib/systemd/system` | LXC Container Initialization and Autoboot Code |
| `lxc@.service` | `usr/lib/systemd/system` | LXC Container: %i |
| `lxcfs.service` | `usr/lib/systemd/system` | FUSE filesystem for LXC |
| `machines.target` | `usr/lib/systemd/system` | Virtual Machines and Containers |
| `machines.target` | `usr/lib/systemd/user` | Virtual Machines and Containers |
| `malcontent-timer-extension-agent.service` | `usr/lib/systemd/system` | Parental Controls Timer Extension Agent |
| `malcontent-timerd.service` | `usr/lib/systemd/system` | Parental Controls Timer Daemon |
| `malcontent-webd-update.service` | `usr/lib/systemd/system` | Regularly Update Parental Controls Web Filtering Daemon |
| `malcontent-webd-update.timer` | `usr/lib/systemd/system` | Regularly update parental controls web filter lists |
| `malcontent-webd.service` | `usr/lib/systemd/system` | Parental Controls Web Filtering Daemon |
| `man-db-cache-update.service` | `usr/lib/systemd/system` | ? |
| `man-db-restart-cache-update.service` | `usr/lib/systemd/system` | ? |
| `mcelog.service` | `usr/lib/systemd/system` | Machine Check Exception Logging Daemon |
| `mdadm-grow-continue@.service` | `usr/lib/systemd/system` | Manage MD Reshape on /dev/%I |
| `mdadm-last-resort@.service` | `usr/lib/systemd/system` | Activate md array %I even though degraded |
| `mdadm-last-resort@.timer` | `usr/lib/systemd/system` | Timer to wait for more drives before activating degraded array %I. |
| `mdcheck_continue.service` | `usr/lib/systemd/system` | MD array scrubbing - continuation |
| `mdcheck_continue.timer` | `usr/lib/systemd/system` | MD array scrubbing - continuation |
| `mdcheck_start.service` | `usr/lib/systemd/system` | MD array scrubbing |
| `mdcheck_start.timer` | `usr/lib/systemd/system` | MD array scrubbing |
| `mdmon@.service` | `usr/lib/systemd/system` | MD Metadata Monitor on %I |
| `mdmonitor-oneshot.service` | `usr/lib/systemd/system` | Reminder for degraded MD arrays |
| `mdmonitor-oneshot.timer` | `usr/lib/systemd/system` | Reminder for degraded MD arrays |
| `mdmonitor.service` | `usr/lib/systemd/system` | Software RAID monitoring and management |
| `mios-account-sync.service` | `usr/lib/systemd/system` | MiOS' live PostgreSQL-to-OS user account sync daemon |
| `mios-additionalimagestores-perms.path` | `usr/lib/systemd/system` | MiOS': watch additionalimagestores for perm changes; retrigger chmod |
| `mios-additionalimagestores-perms.service` | `usr/lib/systemd/system` | MiOS': enforce world-readable perms on /usr/lib/containers/storage |
| `mios-adguard-firstboot.service` | `usr/lib/systemd/system` | MiOS' AdGuard Home first-boot config generator |
| `mios-adguard.container` | `usr/share/containers/systemd` | MiOS' AdGuard Home (DNS ad/tracker/malware sinkhole + resolver) |
| `mios-agent-pipe.service` | `usr/lib/systemd/system` | MiOS' Agent Pipe (router + refine + critic FastAPI; fronts hermes for every gateway) |
| `mios-agent-pipe.socket` | `usr/lib/systemd/system` | MiOS Agent-Pipe Zero-Downtime Socket Activation |
| `mios-agents.service` | `usr/lib/systemd/system` | MiOS' A2O agents super-container (Claude + agy/Gemini + tmux war room + code-server) |
| `mios-ai-firstboot.service` | `usr/lib/systemd/system` | MiOS' AI first-boot provisioning (agent venv + llama.cpp GGUFs) |
| `mios-ai-firstboot.timer` | `usr/lib/systemd/system` | MiOS' AI first-boot provisioning retry (until the sentinel is written) |
| `mios-ai-legacy-forward.service` | `usr/lib/systemd/system` | MiOS' AI Legacy Port Forwarder (8640 -> 8700) |
| `mios-ai.target` | `usr/lib/systemd/system` | MiOS AI Services Target |
| `mios-aios-refresh.service` | `usr/lib/systemd/system` | MiOS' AIOS refresh -- regenerate SSOT-driven role SYSTEMs + discover the A2A fleet |
| `mios-aios-refresh.timer` | `usr/lib/systemd/system` | Periodic MiOS AIOS refresh (SSOT role SYSTEMs + A2A fleet discovery) |
| `mios-attest.service` | `usr/lib/systemd/system` | MiOS RFC 9334 RATS Remote TPM 2.0 Quote Verifier and Zero-Touch Cluster Onboarding Daemon |
| `mios-backup-pgvector.service` | `usr/lib/systemd/system` | MiOS PostgreSQL+pgvector Automated zstd Snapshot Service |
| `mios-backup-pgvector.timer` | `usr/lib/systemd/system` | Daily and Pre-Upgrade Automated PostgreSQL+pgvector Backup Timer |
| `mios-backup-remote.service` | `usr/lib/systemd/system` | MiOS Remote Delta Snapshot Backup Synchronization Service |
| `mios-backup-remote.timer` | `usr/lib/systemd/system` | Daily Off-Site Remote Delta Snapshot Backup Timer |
| `mios-boot-diag.service` | `usr/lib/systemd/system` | MiOS' Boot Diagnostic -- prints service status to console |
| `mios-bootc-switch.path` | `usr/lib/systemd/system` | MiOS' watch for Forgejo Runner build output -> bootc switch |
| `mios-bootc-switch.service` | `usr/lib/systemd/system` | MiOS' bootc-switch from local build sentinel |
| `mios-bound-images-firstboot.service` | `usr/lib/systemd/system` | First-boot Bound Images Provisioner |
| `mios-cdi-detect.service` | `usr/lib/systemd/system` | MiOS' CDI spec detection (WSL vs bare metal vs VM) |
| `mios-ceph-bootstrap.service` | `usr/lib/systemd/system` | MiOS' Ceph Cluster Bootstrap |
| `mios-ceph-snap.service` | `usr/lib/systemd/system` | MiOS CephFS Subvolume Snapshot Service |
| `mios-ceph-snap.timer` | `usr/lib/systemd/system` | Periodic CephFS Subvolume Snapshot Timer |
| `mios-ceph.container` | `usr/share/containers/systemd` | MiOS' Ceph Monitor (Podman-native) |
| `mios-cephfs-quota.service` | `usr/lib/systemd/system` | MiOS CephFS Tenant Dynamic Quota Enforcement Service |
| `mios-cephfs-quota.timer` | `usr/lib/systemd/system` | Periodic CephFS Tenant Dynamic Quota Enforcement Timer |
| `mios-chrony-ptp.service` | `usr/lib/systemd/system` | MiOS Chrony PTP Drop-in Generator |
| `mios-cockpit-link.container` | `usr/share/containers/systemd` | MiOS' Cockpit web-console discovery shim (Podman Desktop link) |
| `mios-cockpit-link.service` | `usr/lib/systemd/system` | MiOS' Cockpit Link Proxy Service |
| `mios-cockpit-link.socket` | `usr/lib/systemd/system` | MiOS' Cockpit Link Proxy Socket |
| `mios-compute.target` | `usr/lib/systemd/system` | MiOS' Compute Role |
| `mios-computer-use-server.service` | `usr/lib/systemd/user` | MiOS' computer-use server (dual MCP + A2A + executor for this desktop) |
| `mios-container-gc.service` | `usr/lib/systemd/system` | MiOS OCI Container Image Storage LRU Garbage Collection |
| `mios-controller.target` | `usr/lib/systemd/system` | MiOS' Controller Role |
| `mios-copy-build-log.service` | `usr/lib/systemd/system` | MiOS' Build Log Copy Service |
| `mios-cpu-isolate.service` | `usr/lib/systemd/system` | MiOS' CPU Core Isolation Engine |
| `mios-cpu-node.container` | `usr/share/containers/systemd` | MiOS' CPU inference node (always-on / gaming-immune last-resort lane) |
| `mios-cron-director.service` | `usr/lib/systemd/system` | MiOS' cron-director (LLM-gated recurring-task scheduler) |
| `mios-daemon.service` | `usr/lib/systemd/system` | MiOS' consolidated micro-LLM daemon (log classify + refusal detect + cron gate) |
| `mios-dashboard-issue.service` | `usr/lib/systemd/system` | MiOS' dashboard -> /etc/issue.d (pre-login banner) |
| `mios-dashboard-issue.timer` | `usr/lib/systemd/system` | MiOS' dashboard /etc/issue.d refresh timer |
| `mios-desktop.target` | `usr/lib/systemd/system` | MiOS' Desktop Role |
| `mios-doc-distill.service` | `usr/lib/systemd/system` | MiOS' daily comment-to-manual distillation |
| `mios-doc-distill.timer` | `usr/lib/systemd/system` | MiOS' daily documentation distillation schedule |
| `mios-embed-backfill.service` | `usr/lib/systemd/system` | MiOS' Embedding Backfill Worker |
| `mios-embed-backfill.timer` | `usr/lib/systemd/system` | MiOS' Embedding Backfill Timer |
| `mios-endpoint.target` | `usr/lib/systemd/system` | MiOS' Endpoint Role |
| `mios-enforcer.container` | `usr/share/containers/systemd` | MiOS' Tetragon eBPF Kernel Enforcement Plane (Standalone Out-of-Process Quarantine) |
| `mios-fhs-sync.service` | `usr/lib/systemd/system` | MiOS' FHS root overlay & host-guest topology runtime sync daemon |
| `mios-finetune-serve.service` | `usr/lib/systemd/system` | MiOS' fine-tune serve (base+adapter refiner backend) |
| `mios-firewall-ports.service` | `usr/lib/systemd/system` | MiOS': ensure firewalld has the MiOS service ports open |
| `mios-firewall.service` | `usr/lib/systemd/system` | MiOS': Declarative nftables container micro-segmentation firewall |
| `mios-firstboot.target` | `usr/lib/systemd/system` | MiOS' first-boot provisioning |
| `mios-flatpak-init.service` | `usr/lib/systemd/system` | MiOS' flatpak override policy (system-wide XDG grants) |
| `mios-flatpak-install.service` | `usr/lib/systemd/system` | MiOS' Flatpak First-Boot Installer |
| `mios-forge-firstboot.service` | `usr/lib/systemd/system` | MiOS' Forge first-boot admin-bootstrap (Forgejo) |
| `mios-forge.container` | `usr/share/containers/systemd` | MiOS' Git forge (Forgejo) |
| `mios-forgejo-runner-firstboot.service` | `usr/lib/systemd/system` | MiOS' Forgejo Runner first-boot registration |
| `mios-forgejo-runner.container` | `usr/share/containers/systemd` | MiOS' Forgejo Runner (self-hosted CI for /=git working tree) |
| `mios-freeipa-enroll.service` | `usr/lib/systemd/system` | MiOS' FreeIPA Zero-Touch Enrollment |
| `mios-git-root-init.service` | `usr/lib/systemd/system` | MiOS' first-boot: init / as a git working tree of localhost:3000/<user>/mios.git |
| `mios-gpu-amd.service` | `usr/lib/systemd/system` | MiOS' AMD GPU container plumbing (ROCm/KFD + DRI) |
| `mios-gpu-detect.service` | `usr/lib/systemd/system` | MiOS' GPU Environment Detection |
| `mios-gpu-intel.service` | `usr/lib/systemd/system` | MiOS' Intel/AMD iGPU Container Plumbing (i915/xe/amdgpu) |
| `mios-gpu-nvidia.service` | `usr/lib/systemd/system` | MiOS' NVIDIA GPU container plumbing |
| `mios-gpu-pv-detect.service` | `usr/lib/systemd/system` | MiOS' Hyper-V GPU-PV Guest Detection |
| `mios-gpu-status.service` | `usr/lib/systemd/system` | MiOS' GPU passthrough detection and status |
| `mios-grd-setup.service` | `usr/lib/systemd/system` | MiOS' GNOME Remote Desktop Setup |
| `mios-guacamole.container` | `usr/share/containers/systemd` | MiOS' Apache Guacamole Web |
| `mios-guacd.container` | `usr/share/containers/systemd` | MiOS' Apache Guacamole Daemon |
| `mios-ha-bootstrap.service` | `usr/lib/systemd/system` | MiOS' HA Cluster Bootstrap (Pacemaker/Corosync) |
| `mios-ha-node.target` | `usr/lib/systemd/system` | MiOS' HA Cluster Node Role |
| `mios-hardware-fallback.service` | `usr/lib/systemd/user` | MiOS' Automated Network and Audio Fallback Manager |
| `mios-headless.target` | `usr/lib/systemd/system` | MiOS' Headless Role |
| `mios-headscale-firstboot.service` | `usr/lib/systemd/system` | MiOS' Headscale mesh VPN coordinator first-boot config generator |
| `mios-headscale.container` | `usr/share/containers/systemd` | MiOS' Headscale Mesh VPN Control Plane Coordinator |
| `mios-hermes-browser-worker.service` | `usr/lib/systemd/system` | MiOS' Hermes-Browser-Worker (ChromeDev CDP :9223 for the worker) |
| `mios-hermes-browser.service` | `usr/lib/systemd/system` | MiOS' Hermes-Browser (ChromeDev w/ CDP for Hermes-Agent) |
| `mios-hermes-firstboot.service` | `usr/lib/systemd/system` | MiOS' Hermes-Agent first-boot config + key generation |
| `mios-hermes-tail.service` | `usr/lib/systemd/system` | MiOS' Hermes journal tail (in-flight status -> OWUI emitter) |
| `mios-hybrid.target` | `usr/lib/systemd/system` | MiOS' Hybrid role (desktop + k3s-worker + ceph-osd) |
| `mios-hyperv-enhanced.service` | `usr/lib/systemd/system` | MiOS' Hyper-V Enhanced Session Setup (gnome-remote-desktop) |
| `mios-k3s-init.service` | `usr/lib/systemd/system` | MiOS' K3s Manifest Seeder |
| `mios-k3s-master.target` | `usr/lib/systemd/system` | MiOS' K3s Master Role |
| `mios-k3s-worker.target` | `usr/lib/systemd/system` | MiOS' K3s worker role (agent) |
| `mios-k3s.container` | `usr/share/containers/systemd` | MiOS' K3s Service (Podman-native) |
| `mios-keyring-autounlock.service` | `usr/lib/systemd/user` | MiOS' gnome-keyring auto-unlock with mios.toml password |
| `mios-kvmfr-load.service` | `usr/lib/systemd/system` | Load kvmfr kernel module (Looking Glass shared memory device) |
| `mios-launcher.service` | `usr/lib/systemd/user` | MiOS' launcher broker (operator-side) |
| `mios-ledger-sync.service` | `usr/lib/systemd/system` | MiOS CephFS Transactional Ledger Replication Service |
| `mios-libexec-perms.path` | `usr/lib/systemd/system` | MiOS': watch /usr/libexec/mios for perm changes; retrigger chmod |
| `mios-libexec-perms.service` | `usr/lib/systemd/system` | MiOS': enforce exec perms (go+rX) on /usr/libexec/mios |
| `mios-libvirtd-setup.service` | `usr/lib/systemd/system` | MiOS' first-boot libvirtd wiring |
| `mios-llm-heavy.container` | `usr/share/containers/systemd` | MiOS' heavy lane, vLLM engine (OpenAI /v1, PagedAttention + APC; gated) |
| `mios-llm-light.container` | `usr/share/containers/systemd` | MiOS' LLM-Light (llama.cpp multi-model + KV-paging lane, served via the upstream llama-swap proxy, FOSS) |
| `mios-llm-worker@.container` | `usr/share/containers/systemd` | MiOS' swarm worker %i (single-model llama-server, FOSS) |
| `mios-log-archiver.service` | `usr/lib/systemd/system` | MiOS Structured Parquet Log Archiver and Vector Indexer |
| `mios-log-archiver.timer` | `usr/lib/systemd/system` | Hourly MiOS Journal Log Parquet Compaction Timer |
| `mios-log-streamer.service` | `usr/lib/systemd/system` | MiOS Unified Journald Log Aggregation & pgvector Vector Streamer |
| `mios-luks-enroll.service` | `usr/lib/systemd/system` | Enroll LUKS keys automatically using TPM2 |
| `mios-luks-rotate.service` | `usr/lib/systemd/system` | MiOS LUKS2 Drive Encryption Key Rotation Service |
| `mios-luks-rotate.timer` | `usr/lib/systemd/system` | Monthly Automated LUKS2 Disk Encryption Key Rotation Timer |
| `mios-mcp.service` | `usr/lib/systemd/system` | MiOS' Agent Context Service (MCP) |
| `mios-mdns-mesh.service` | `usr/lib/systemd/system` | MiOS Zero-Configuration mDNS Peer Discovery & WireGuard Peering Daemon |
| `mios-models-firstboot.service` | `usr/lib/systemd/system` | First-boot Large-model Provisioner |
| `mios-mok-enroll.service` | `usr/lib/systemd/system` | MiOS' first-boot MOK enrollment for Secure Boot UKI trust |
| `mios-node.container` | `usr/share/containers/systemd` | MiOS' Distributed Edge Micro-Node Quadlet Service |
| `mios-node.service` | `usr/lib/systemd/system` | MiOS ('My OS') Distributed Edge Micro-Node Runtime Daemon |
| `mios-oci-delta-apply.service` | `usr/lib/systemd/system` | MiOS OCI Delta Apply Service (GAP-5) |
| `mios-offline-upgrade.service` | `usr/lib/systemd/system` | MiOS Offline Atomic OCI Upgrade Service |
| `mios-open-webui.container` | `usr/share/containers/systemd` | MiOS' Open WebUI |
| `mios-opencode-gateway.service` | `usr/lib/systemd/system` | MiOS' OpenCode /v1 gateway (OpenAI adapter fronting the opencode CLI) |
| `mios-otelcol.container` | `usr/share/containers/systemd` | MiOS' OpenTelemetry Collector and Jaeger Trace Viewer |
| `mios-passport-provision.service` | `usr/lib/systemd/system` | MiOS' Phase C.3 agent passport provisioning (Ed25519 keypairs) |
| `mios-pg-replica.service` | `usr/lib/systemd/system` | MiOS PostgreSQL Hot-Standby Streaming Replication Service |
| `mios-pgvector-backup.service` | `usr/lib/systemd/system` | MiOS' daily pg_dump backup of the unified agent-plane datastore (pgvector) |
| `mios-pgvector-backup.timer` | `usr/lib/systemd/system` | MiOS' daily pgvector datastore backup schedule |
| `mios-pgvector-major-upgrade.service` | `usr/lib/systemd/system` | MiOS' pgvector PostgreSQL-major migration guard |
| `mios-pgvector-optimize.service` | `usr/lib/systemd/system` | MiOS pgvector Database Optimization Service |
| `mios-pgvector-optimize.timer` | `usr/lib/systemd/system` | Weekly Automated pgvector Index & Table Maintenance Timer |
| `mios-pgvector.container` | `usr/share/containers/systemd` | MiOS' PostgreSQL + pgvector (unified agent-plane datastore, FOSS) |
| `mios-piper.container` | `usr/share/containers/systemd` | MiOS' Piper Streaming TTS Engine (piper1-gpl http_server) |
| `mios-podman-gc.service` | `usr/lib/systemd/system` | MiOS' Podman Garbage Collection |
| `mios-podman-gc.timer` | `usr/lib/systemd/system` | Weekly Podman Cleanup |
| `mios-podman-ps.service` | `usr/lib/systemd/system` | MiOS' rootful podman snapshot for the agent-pipe portal/dashboard |
| `mios-podman-ps.timer` | `usr/lib/systemd/system` | MiOS' refresh the podman container snapshot for the dashboard |
| `mios-policy-arbiter.service` | `usr/lib/systemd/system` | MiOS' out-of-process HITL policy arbiter (WS-9) |
| `mios-powerd.service` | `usr/lib/systemd/system` | MiOS' Hardware Power Supply Monitor and Battery-Aware AI Downscaler Daemon |
| `mios-pstore.service` | `usr/lib/systemd/system` | MiOS Persistent Kernel Crash Buffer Extractor |
| `mios-pxe-hub.container` | `usr/share/containers/systemd` | MiOS' PXE Boot Hub |
| `mios-radosgw.container` | `usr/share/containers/systemd` | MiOS' Ceph RADOS Gateway (S3 Object Storage) |
| `mios-registry.service` | `usr/lib/systemd/system` | MiOS Local OCI Distribution Registry (task_454) |
| `mios-role.service` | `usr/lib/systemd/system` | MiOS' System Init & Role Engine |
| `mios-searxng.container` | `usr/share/containers/systemd` | MiOS' SearXNG metasearch (privacy-respecting search proxy) |
| `mios-secret-init.service` | `usr/lib/systemd/system` | MiOS Quadlet Secrets Environment Initialization |
| `mios-self-heal.service` | `usr/lib/systemd/system` | MiOS Autonomous Self-Healing Code Remediation Agent |
| `mios-selinux-init.service` | `usr/lib/systemd/system` | MiOS' SELinux Policy Loader |
| `mios-shell-session-gc.service` | `usr/lib/systemd/system` | Reap idle MiOS shell sessions |
| `mios-shell-session-gc.timer` | `usr/lib/systemd/system` | Periodic reap of idle MiOS shell sessions |
| `mios-skills-miner.service` | `usr/lib/systemd/system` | MiOS' Phase C.2 Sequential Pattern Mining over tool_call history |
| `mios-skills-miner.timer` | `usr/lib/systemd/system` | MiOS' Phase C.2 skill-miner cadence (sequential pattern mining) |
| `mios-sriov-init.service` | `usr/lib/systemd/system` | MiOS' Universal SR-IOV Initialization |
| `mios-suggestion-refresh.service` | `usr/lib/systemd/system` | MiOS' starter-chip refresh (revolving suggestions) |
| `mios-suggestion-refresh.timer` | `usr/lib/systemd/system` | MiOS' starter-chip refresh cadence |
| `mios-swarm-pack-firstboot.service` | `usr/lib/systemd/system` | MiOS' swarm small-model pack arming (gpu_profile=swarm only) |
| `mios-sync-theme.service` | `usr/lib/systemd/system` | MiOS theme bridge -- regenerate /etc/mios/theme from mios.toml [colors] |
| `mios-sys-env-refresh.service` | `usr/lib/systemd/system` | MiOS' refresh the live system/environment cache (sys_env) in pgvector |
| `mios-sys-env-refresh.timer` | `usr/lib/systemd/system` | MiOS' refresh cadence for the sys_env environment cache |
| `mios-thermald.service` | `usr/lib/systemd/system` | MiOS Proactive PID Thermal Daemon and Dynamic Power Cap Modulator |
| `mios-tmpfs-spill.service` | `usr/lib/systemd/system` | MiOS Automated tmpfs Spill-to-NVMe Manager |
| `mios-tmpfs-spill.timer` | `usr/lib/systemd/system` | MiOS Periodic Memory Pressure & tmpfs Spillover Monitor Timer |
| `mios-ttyd-bash.service` | `usr/lib/systemd/system` | MiOS' ttyd -- browser pty bridge (Linux bash session) |
| `mios-ttyd-expose.service` | `usr/lib/systemd/system` | MiOS' mobile terminal tailnet exposure (gated by [ttyd].tailnet_expose) |
| `mios-ttyd-powershell.service` | `usr/lib/systemd/system` | MiOS' ttyd -- browser pty bridge (Windows PowerShell session) |
| `mios-user-snapshot@.service` | `usr/lib/systemd/system` | Hourly CephFS delta snapshot and mesh replication for user %i |
| `mios-user-snapshot@.timer` | `usr/lib/systemd/system` | Hourly CephFS delta snapshot schedule for user %i |
| `mios-userdb-render.service` | `usr/lib/systemd/system` | MiOS' PostgreSQL account systemd userdb drop-in renderer |
| `mios-verify-root.service` | `usr/lib/systemd/system` | MiOS' Root Filesystem Verification |
| `mios-verify.service` | `usr/lib/systemd/system` | MiOS' Cryptographic Integrity Audit (fs-verity) |
| `mios-vfio-setup.service` | `usr/lib/systemd/system` | MiOS VFIO & Looking Glass B6 IVSHMEM Setup |
| `mios-vpn-killswitch.service` | `usr/lib/systemd/system` | MiOS Declarative nftables VPN Kill-Switch and Split-Tunnel Guard |
| `mios-wakeword.service` | `usr/lib/systemd/user` | MiOS Three-Stage Acoustic Wake-Word Daemon |
| `mios-wallpaper.service` | `usr/lib/systemd/user` | MiOS' Living Wallpaper Occlusion Engine Daemon |
| `mios-waydroid-init.service` | `usr/lib/systemd/system` | MiOS' Waydroid Android Initialization |
| `mios-webtools-crawl4ai.container` | `usr/share/containers/systemd` | MiOS' web-tools crawl4ai slim engine (Chrome-CDP primary + camoufox fallback) |
| `mios-webtools-firecrawl-api.container` | `usr/share/containers/systemd` | MiOS' web-tools firecrawl API (v1.0.0, self-host no-auth) |
| `mios-webtools-firecrawl-worker.container` | `usr/share/containers/systemd` | MiOS' web-tools firecrawl worker (Bull queue processor) |
| `mios-webtools-firstboot.service` | `usr/lib/systemd/system` | MiOS' web-tools images build-on-demand firstboot service |
| `mios-webtools-redis.container` | `usr/share/containers/systemd` | MiOS' web-tools redis (firecrawl queue + ratelimit store) |
| `mios-whisper.container` | `usr/share/containers/systemd` | MiOS' Whisper Streaming STT Engine (whisper.cpp) |
| `mios-wol-proxy.service` | `usr/lib/systemd/system` | MiOS Signed Proxy Wake-on-LAN Daemon with SecureON Payload |
| `mios-wsl-early.service` | `usr/lib/systemd/system` | MiOS' WSL2 pre-sysinit fixups (rshared root + /dev/{net/tun,fuse}) |
| `mios-wsl-env-import.service` | `usr/lib/systemd/user` | MiOS': import WSLg env into systemd user-bus + dbus activation |
| `mios-wsl-firstboot.service` | `usr/lib/systemd/system` | MiOS' WSL2 First Boot Initialization |
| `mios-wsl-flatpak-export-sync.path` | `usr/lib/systemd/system` | MiOS' Re-fire flatpak->WSL .desktop sync when flatpak installs/uninstalls land |
| `mios-wsl-flatpak-export-sync.service` | `usr/lib/systemd/system` | MiOS' Sync flatpak .desktop + icon exports into /usr/share so WSL Start Menu picks them up |
| `mios-wsl-flatpak-heal.service` | `usr/lib/systemd/user` | MiOS' WSL2g flatpak portal heal -- restart inactive portals |
| `mios-wsl-flatpak-heal.timer` | `usr/lib/systemd/user` | MiOS' WSL2g flatpak portal heal cadence |
| `mios-wsl-graphical-session.service` | `usr/lib/systemd/user` | MiOS': pull in graphical-session.target on WSLg |
| `mios-wsl-init.service` | `usr/lib/systemd/system` | MiOS' WSL2 Runtime Bridge |
| `mios-wsl-interop-priority.service` | `usr/lib/systemd/system` | MiOS': keep WSL interop primary for Windows .exe (disable WINE binfmt shadowing /mnt/c/*.exe) |
| `mios-wsl-runtime-dir.service` | `usr/lib/systemd/system` | MiOS' WSL2 user-runtime-dir fallback |
| `mios-wsl-theme-bridge.service` | `usr/lib/systemd/user` | MiOS' WSL theme bridge (Windows light/dark -> GNOME color-scheme) |
| `mios-wslg-env.service` | `usr/lib/systemd/user` | Import WSLg display environment into the systemd --user manager |
| `mios-wslg-permissions-fix.service` | `usr/lib/systemd/system` | MiOS': chmod /mnt/wslg/runtime-dir to 0700 so weston accepts it as XDG_RUNTIME_DIR |
| `mios-xdg-userdir-init.service` | `usr/lib/systemd/system` | MiOS XDG User Directories Initialization |
| `miosd.service` | `usr/lib/systemd/system` | MiOS Native System Daemon & Supervisor (Rust miosd) |
| `mobi.phosh.OSK.service` | `usr/lib/systemd/user` | Stevia, a user friendly on screen keyboard |
| `mobi.phosh.OSK.target` | `usr/lib/systemd/user` | Phosh On Screen Keyboard |
| `mobi.phosh.Shell.service` | `usr/lib/systemd/user` | Phosh, a shell for mobile phones |
| `mobi.phosh.Shell.target` | `usr/lib/systemd/user` | Phosh |
| `modprobe@.service` | `usr/lib/systemd/system` | Load Kernel Module %i |
| `mpris-proxy.service` | `usr/lib/systemd/user` | Bluetooth mpris proxy |
| `multi-user.target` | `usr/lib/systemd/system` | Multi-User System |
| `multipathd-queueing.service` | `usr/lib/systemd/system` | Enable queuing for multipath maps |
| `multipathd.service` | `usr/lib/systemd/system` | Device-Mapper Multipath Device Controller |
| `multipathd.socket` | `usr/lib/systemd/system` | multipathd control socket |
| `netavark-dhcp-proxy.service` | `usr/lib/systemd/system` | Netavark DHCP proxy service |
| `netavark-dhcp-proxy.socket` | `usr/lib/systemd/system` | Netavark DHCP proxy socket |
| `netavark-firewalld-reload.service` | `usr/lib/systemd/system` | Listen for the firewalld reload event and reapply all netavark firewall rules. |
| `netavark-nftables-reload.service` | `usr/lib/systemd/system` | Restore Netavark firewall rules after nftables service is reloaded |
| `network-online.target` | `usr/lib/systemd/system` | Network is Online |
| `network-pre.target` | `usr/lib/systemd/system` | Preparation for Network |
| `network.target` | `usr/lib/systemd/system` | Network |
| `nfs-client.target` | `usr/lib/systemd/system` | NFS client services |
| `nfs-idmapd.service` | `usr/lib/systemd/system` | NFSv4 ID-name mapping service |
| `nfs-mountd.service` | `usr/lib/systemd/system` | NFS Mount Daemon |
| `nfs-server.service` | `usr/lib/systemd/system` | NFS server and services |
| `nfs-utils.service` | `usr/lib/systemd/system` | NFS server and client services |
| `nfsdcld.service` | `usr/lib/systemd/system` | NFSv4 Client Tracking Daemon |
| `nftables.service` | `usr/lib/systemd/system` | Netfilter Tables |
| `nis-domainname.service` | `usr/lib/systemd/system` | Read and set NIS domainname from /etc/sysconfig/network |
| `nm-priv-helper.service` | `usr/lib/systemd/system` | NetworkManager Privileged Helper |
| `nmb.service` | `usr/lib/systemd/system` | Samba NMB Daemon |
| `nss-lookup.target` | `usr/lib/systemd/system` | Host and Network Name Lookups |
| `nss-user-lookup.target` | `usr/lib/systemd/system` | User and Group Name Lookups |
| `numad.service` | `usr/lib/systemd/system` | numad - The NUMA daemon that manages application locality. |
| `nvmefc-boot-connections.service` | `usr/lib/systemd/system` | Auto-connect to subsystems on FC-NVME devices found during boot |
| `nvmf-autoconnect.service` | `usr/lib/systemd/system` | Connect NVMe-oF subsystems automatically during boot |
| `nvmf-connect-nbft.service` | `usr/lib/systemd/system` | Connect NBFT-defined NVMe-oF subsystems automatically |
| `nvmf-connect.target` | `usr/lib/systemd/system` | All instances of nvmf-autoconnect daemon |
| `nvmf-connect@.service` | `usr/lib/systemd/system` | NVMf auto-connect scan upon nvme discovery controller Events |
| `obex.service` | `usr/lib/systemd/user` | Bluetooth OBEX service |
| `oddjobd.service` | `usr/lib/systemd/system` | privileged operations for unprivileged applications |
| `openvpn-client@.service` | `usr/lib/systemd/system` | OpenVPN tunnel for %i |
| `openvpn-server@.service` | `usr/lib/systemd/system` | OpenVPN service for %i |
| `org.freedesktop.IBus.session.GNOME.service` | `usr/lib/systemd/user` | IBus Daemon for GNOME |
| `org.freedesktop.IBus.session.generic.service` | `usr/lib/systemd/user` | IBus Daemon for generic sessions |
| `org.gnome.SettingsDaemon.A11ySettings.service` | `usr/lib/systemd/user` | GNOME accessibility service |
| `org.gnome.SettingsDaemon.A11ySettings.target` | `usr/lib/systemd/user` | GNOME accessibility target |
| `org.gnome.SettingsDaemon.Color.service` | `usr/lib/systemd/user` | GNOME color management service |
| `org.gnome.SettingsDaemon.Color.target` | `usr/lib/systemd/user` | GNOME color management target |
| `org.gnome.SettingsDaemon.Datetime.service` | `usr/lib/systemd/user` | GNOME date & time service |
| `org.gnome.SettingsDaemon.Datetime.target` | `usr/lib/systemd/user` | GNOME date & time target |
| `org.gnome.SettingsDaemon.Housekeeping.service` | `usr/lib/systemd/user` | GNOME maintenance of expirable data service |
| `org.gnome.SettingsDaemon.Housekeeping.target` | `usr/lib/systemd/user` | GNOME maintenance of expirable data target |
| `org.gnome.SettingsDaemon.Keyboard.service` | `usr/lib/systemd/user` | GNOME keyboard configuration service |
| `org.gnome.SettingsDaemon.Keyboard.target` | `usr/lib/systemd/user` | GNOME keyboard configuration target |
| `org.gnome.SettingsDaemon.MediaKeys.service` | `usr/lib/systemd/user` | GNOME keyboard shortcuts service |
| `org.gnome.SettingsDaemon.MediaKeys.target` | `usr/lib/systemd/user` | GNOME keyboard shortcuts target |
| `org.gnome.SettingsDaemon.Power.service` | `usr/lib/systemd/user` | GNOME power management service |
| `org.gnome.SettingsDaemon.Power.target` | `usr/lib/systemd/user` | GNOME power management target |
| `org.gnome.SettingsDaemon.PrintNotifications.service` | `usr/lib/systemd/user` | GNOME printer notifications service |
| `org.gnome.SettingsDaemon.PrintNotifications.target` | `usr/lib/systemd/user` | GNOME printer notifications target |
| `org.gnome.SettingsDaemon.Rfkill.service` | `usr/lib/systemd/user` | GNOME RFKill support service |
| `org.gnome.SettingsDaemon.Rfkill.target` | `usr/lib/systemd/user` | GNOME RFKill support target |
| `org.gnome.SettingsDaemon.ScreensaverProxy.service` | `usr/lib/systemd/user` | GNOME FreeDesktop screensaver service |
| `org.gnome.SettingsDaemon.ScreensaverProxy.target` | `usr/lib/systemd/user` | GNOME FreeDesktop screensaver target |
| `org.gnome.SettingsDaemon.Sharing.service` | `usr/lib/systemd/user` | GNOME file sharing service |
| `org.gnome.SettingsDaemon.Sharing.target` | `usr/lib/systemd/user` | GNOME file sharing target |
| `org.gnome.SettingsDaemon.Smartcard.service` | `usr/lib/systemd/user` | GNOME smartcard service |
| `org.gnome.SettingsDaemon.Smartcard.target` | `usr/lib/systemd/user` | GNOME smartcard target |
| `org.gnome.SettingsDaemon.Sound.service` | `usr/lib/systemd/user` | GNOME sound sample caching service |
| `org.gnome.SettingsDaemon.Sound.target` | `usr/lib/systemd/user` | GNOME sound sample caching target |
| `org.gnome.SettingsDaemon.UsbProtection.service` | `usr/lib/systemd/user` | GNOME USB protection service |
| `org.gnome.SettingsDaemon.UsbProtection.target` | `usr/lib/systemd/user` | GNOME USB protection target |
| `org.gnome.SettingsDaemon.Wwan.service` | `usr/lib/systemd/user` | GNOME WWan support service |
| `org.gnome.SettingsDaemon.Wwan.target` | `usr/lib/systemd/user` | GNOME WWan support target |
| `org.gnome.SettingsDaemon.XSettings.service` | `usr/lib/systemd/user` | GNOME XSettings service |
| `org.gnome.SettingsDaemon.XSettings.target` | `usr/lib/systemd/user` | GNOME XSettings target |
| `org.gnome.Shell-disable-extensions.service` | `usr/lib/systemd/user` | Disable GNOME Shell extensions after failure |
| `org.gnome.Shell@.service` | `usr/lib/systemd/user` | GNOME Shell |
| `osbuild-composer-api.socket` | `usr/lib/systemd/system` | OSBuild Composer API socket |
| `osbuild-composer-prometheus.socket` | `usr/lib/systemd/system` | OSBuild Composer prometheus socket |
| `osbuild-composer.service` | `usr/lib/systemd/system` | OSBuild Composer |
| `osbuild-composer.socket` | `usr/lib/systemd/system` | OSBuild Composer Weldr API socket |
| `osbuild-local-worker.socket` | `usr/lib/systemd/system` | OSBuild local worker API socket |
| `osbuild-remote-worker.socket` | `usr/lib/systemd/system` | OSBuild remote worker API socket |
| `osbuild-remote-worker@.service` | `usr/lib/systemd/system` | OSBuild Composer Remote Worker (%i) |
| `osbuild-worker@.service` | `usr/lib/systemd/system` | OSBuild Composer Worker (%i) |
| `ostree-boot-complete.service` | `usr/lib/systemd/system` | OSTree Complete Boot |
| `ostree-finalize-staged-hold.service` | `usr/lib/systemd/system` | Hold /boot Open for OSTree Finalize Staged Deployment |
| `ostree-finalize-staged.service` | `usr/lib/systemd/system` | OSTree Finalize Staged Deployment |
| `ostree-prepare-root.service` | `usr/lib/systemd/system` | OSTree Prepare OS/ |
| `ostree-remount.service` | `usr/lib/systemd/system` | OSTree Remount OS/ Bind Mounts |
| `ostree-state-overlay@.service` | `usr/lib/systemd/system` | OSTree State Overlay On /%I |
| `p11-kit-server.service` | `usr/lib/systemd/user` | p11-kit server |
| `p11-kit-server.socket` | `usr/lib/systemd/user` | p11-kit server |
| `pacemaker.service` | `usr/lib/systemd/system` | Pacemaker High Availability Cluster Manager |
| `packagekit-offline-update.service` | `usr/lib/systemd/system` | Update the operating system whilst offline |
| `packagekit.service` | `usr/lib/systemd/system` | PackageKit Daemon |
| `pam_namespace.service` | `usr/lib/systemd/system` | Make sure parent directories configured in /etc/security/namespace.conf for polyinstantiation exist |
| `paths.target` | `usr/lib/systemd/system` | Path Units |
| `paths.target` | `usr/lib/systemd/user` | Paths |
| `pcp-reboot-init.service` | `usr/lib/systemd/system` | PCP Reboot Initialization Helper Service |
| `pcscd.service` | `usr/lib/systemd/system` | PC/SC Smart Card Daemon |
| `pcscd.socket` | `usr/lib/systemd/system` | PC/SC Smart Card Daemon Activation Socket |
| `pcsd-ruby.service` | `usr/lib/systemd/system` | PCS GUI and remote configuration interface (Ruby) |
| `pcsd.service` | `usr/lib/systemd/system` | PCS GUI and remote configuration interface |
| `pipewire-pulse.service` | `usr/lib/systemd/user` | PipeWire PulseAudio |
| `pipewire-pulse.socket` | `usr/lib/systemd/user` | PipeWire PulseAudio |
| `pipewire.service` | `usr/lib/systemd/user` | PipeWire Multimedia Service |
| `pipewire.socket` | `usr/lib/systemd/user` | PipeWire Multimedia System Sockets |
| `plocate-updatedb.service` | `usr/lib/systemd/system` | Update the plocate database |
| `plocate-updatedb.timer` | `usr/lib/systemd/system` | Update the plocate database daily |
| `plymouth-halt.service` | `usr/lib/systemd/system` | Show Plymouth Halt Screen |
| `plymouth-kexec.service` | `usr/lib/systemd/system` | Show Plymouth Reboot with kexec Screen |
| `plymouth-poweroff.service` | `usr/lib/systemd/system` | Show Plymouth Power Off Screen |
| `plymouth-quit-wait.service` | `usr/lib/systemd/system` | Hold until boot process finishes up |
| `plymouth-quit.service` | `usr/lib/systemd/system` | Terminate Plymouth Boot Screen |
| `plymouth-read-write.service` | `usr/lib/systemd/system` | Tell Plymouth To Write Out Runtime Data |
| `plymouth-reboot.service` | `usr/lib/systemd/system` | Show Plymouth Reboot Screen |
| `plymouth-start.service` | `usr/lib/systemd/system` | Show Plymouth Boot Screen |
| `plymouth-switch-root-initramfs.service` | `usr/lib/systemd/system` | Tell Plymouth To Jump To initramfs |
| `plymouth-switch-root.service` | `usr/lib/systemd/system` | Plymouth switch root service |
| `pmcd.service` | `usr/lib/systemd/system` | Performance Metrics Collector Daemon |
| `pmfind.service` | `usr/lib/systemd/system` | Discover remote PCP services for analysis |
| `pmfind.timer` | `usr/lib/systemd/system` | Polled discovery of PCP services for analysis |
| `pmie.service` | `usr/lib/systemd/system` | Performance Metrics Inference Engine |
| `pmie_check.service` | `usr/lib/systemd/system` | Check PMIE instances are running |
| `pmie_check.timer` | `usr/lib/systemd/system` | Half-hourly check of PMIE instances |
| `pmie_daily.service` | `usr/lib/systemd/system` | Process PMIE logs |
| `pmie_daily.timer` | `usr/lib/systemd/system` | Daily processing of PMIE logs |
| `pmie_farm.service` | `usr/lib/systemd/system` | pmie farm service |
| `pmie_farm_check.service` | `usr/lib/systemd/system` | Check and migrate non-primary pmie farm instances |
| `pmie_farm_check.timer` | `usr/lib/systemd/system` | Half-hourly check of pmie farm instances |
| `pmlogger.service` | `usr/lib/systemd/system` | PCP primary archive logger |
| `pmlogger_check.service` | `usr/lib/systemd/system` | PCP archive loggers (non-primary and remote hosts) |
| `pmlogger_check.timer` | `usr/lib/systemd/system` | Half-hourly check for PCP archive loggers (non-primary and remote hosts) |
| `pmlogger_daily.service` | `usr/lib/systemd/system` | Daily processing of local PCP archives |
| `pmlogger_daily.timer` | `usr/lib/systemd/system` | Daily processing of local PCP archives |
| `pmlogger_farm.service` | `usr/lib/systemd/system` | pmlogger farm service |
| `pmlogger_farm_check.service` | `usr/lib/systemd/system` | Check PCP archive loggers |
| `pmlogger_farm_check.timer` | `usr/lib/systemd/system` | Half-hourly check of PCP archive logger |
| `pmproxy.service` | `usr/lib/systemd/system` | Proxy for Performance Metrics Collector Daemon |
| `pmproxy_check.service` | `usr/lib/systemd/system` | Optionally compress remote PCP archives from pmproxy |
| `pmproxy_check.timer` | `usr/lib/systemd/system` | Half-hourly optional compression of remote PCP archives from pmproxy |
| `pmproxy_daily.service` | `usr/lib/systemd/system` | Daily processing of remote PCP archives from pmproxy |
| `pmproxy_daily.timer` | `usr/lib/systemd/system` | Daily processing of remote PCP archives from pmproxy |
| `podman-auto-update.service` | `usr/lib/systemd/system` | Podman auto-update service |
| `podman-auto-update.service` | `usr/lib/systemd/user` | Podman auto-update service |
| `podman-auto-update.timer` | `usr/lib/systemd/system` | Podman auto-update timer |
| `podman-auto-update.timer` | `usr/lib/systemd/user` | Podman auto-update timer |
| `podman-clean-transient.service` | `usr/lib/systemd/system` | Clean up podman transient data |
| `podman-clean-transient.service` | `usr/lib/systemd/user` | Clean up podman transient data |
| `podman-kube@.service` | `usr/lib/systemd/system` | A template for running K8s workloads via podman-kube-play |
| `podman-kube@.service` | `usr/lib/systemd/user` | A template for running K8s workloads via podman-kube-play |
| `podman-restart.service` | `usr/lib/systemd/system` | Podman Start All Containers With Restart Policy Set To Always |
| `podman-restart.service` | `usr/lib/systemd/user` | Podman Start All Containers With Restart Policy Set To Always |
| `podman-user-wait-network-online.service` | `usr/lib/systemd/user` | Wait for system level network-online.target as user. |
| `podman.service` | `usr/lib/systemd/system` | Podman API Service |
| `podman.service` | `usr/lib/systemd/user` | Podman API Service |
| `podman.socket` | `usr/lib/systemd/system` | Podman API Socket |
| `podman.socket` | `usr/lib/systemd/user` | Podman API Socket |
| `polkit-agent-helper.socket` | `usr/lib/systemd/system` | Authorization Manager Agent Helper |
| `polkit-agent-helper@.service` | `usr/lib/systemd/system` | Authorization Manager Agent Helper |
| `polkit.service` | `usr/lib/systemd/system` | Authorization Manager |
| `poweroff.target` | `usr/lib/systemd/system` | System Power Off |
| `printer.target` | `usr/lib/systemd/system` | Printer Support |
| `printer.target` | `usr/lib/systemd/user` | Printer |
| `proc-fs-nfsd.mount` | `usr/lib/systemd/system` | NFSD configuration filesystem |
| `proc-sys-fs-binfmt_misc.automount` | `usr/lib/systemd/system` | Arbitrary Executable File Formats File System Automount Point |
| `proc-sys-fs-binfmt_misc.mount` | `usr/lib/systemd/system` | Arbitrary Executable File Formats File System |
| `psacct.service` | `usr/lib/systemd/system` | Kernel process accounting |
| `qemu-guest-agent.service` | `usr/lib/systemd/system` | QEMU Guest Agent |
| `qemu-pr-helper.service` | `usr/lib/systemd/system` | Persistent Reservation Daemon for QEMU |
| `qemu-pr-helper.socket` | `usr/lib/systemd/system` | Persistent Reservation Daemon for QEMU |
| `qemu-vmsr-helper.service` | `usr/lib/systemd/system` | Virtual RAPL MSR Daemon for QEMU |
| `qemu-vmsr-helper.socket` | `usr/lib/systemd/system` | Virtual RAPL MSR helper for QEMU |
| `quotaon-root.service` | `usr/lib/systemd/system` | Enable Root File System Quotas |
| `quotaon@.service` | `usr/lib/systemd/system` | Enable File System Quotas |
| `raid-check.service` | `usr/lib/systemd/system` | RAID setup health check |
| `raid-check.timer` | `usr/lib/systemd/system` | Weekly RAID setup health check |
| `rbdmap.service` | `usr/lib/systemd/system` | Map RBD devices |
| `rc-local.service` | `usr/lib/systemd/system` | /etc/rc.d/rc.local Compatibility |
| `realmd.service` | `usr/lib/systemd/system` | Realm and Domain Configuration |
| `reboot.target` | `usr/lib/systemd/system` | System Reboot |
| `remote-cryptsetup.target` | `usr/lib/systemd/system` | Remote Encrypted Volumes |
| `remote-fs-pre.target` | `usr/lib/systemd/system` | Preparation for Remote File Systems |
| `remote-fs.target` | `usr/lib/systemd/system` | Remote File Systems |
| `remote-integritysetup.target` | `usr/lib/systemd/system` | Remote Integrity Protected Volumes |
| `remote-veritysetup.target` | `usr/lib/systemd/system` | Remote Verity Protected Volumes |
| `rescue.service` | `usr/lib/systemd/system` | Rescue Shell |
| `rescue.target` | `usr/lib/systemd/system` | Rescue Mode |
| `resource-agents-deps.target` | `usr/lib/systemd/system` | resource-agents dependencies |
| `rpc-gssd.service` | `usr/lib/systemd/system` | RPC security service for NFS client and server |
| `rpc-statd-notify.service` | `usr/lib/systemd/system` | Notify NFS peers of a restart |
| `rpc-statd.service` | `usr/lib/systemd/system` | NFS status monitor for NFSv2/3 locking. |
| `rpc_pipefs.target` | `usr/lib/systemd/system` | ? |
| `rpcbind.service` | `usr/lib/systemd/system` | RPC Bind |
| `rpcbind.socket` | `usr/lib/systemd/system` | RPCbind Server Activation Socket |
| `rpcbind.target` | `usr/lib/systemd/system` | RPC Port Mapper |
| `rpm-ostree-bootstatus.service` | `usr/lib/systemd/system` | Log rpm-ostree Booted Deployment Status To Journal |
| `rpm-ostree-countme.service` | `usr/lib/systemd/system` | Weekly rpm-ostree Count Me reporting |
| `rpm-ostree-countme.timer` | `usr/lib/systemd/system` | Weekly rpm-ostree Count Me timer |
| `rpm-ostree-fix-shadow-mode.service` | `usr/lib/systemd/system` | Update permissions for /etc/shadow |
| `rpm-ostreed-automatic.service` | `usr/lib/systemd/system` | rpm-ostree Automatic Update |
| `rpm-ostreed-automatic.timer` | `usr/lib/systemd/system` | rpm-ostree Automatic Update Trigger |
| `rpm-ostreed.service` | `usr/lib/systemd/system` | rpm-ostree System Management Daemon |
| `rpmdb-rebuild.service` | `usr/lib/systemd/system` | RPM database rebuild |
| `rsyslog.service` | `usr/lib/systemd/system` | System Logging Service |
| `rtkit-daemon.service` | `usr/lib/systemd/system` | RealtimeKit Scheduling Policy Service |
| `runlevel0.target` | `usr/lib/systemd/system` | System Power Off |
| `runlevel1.target` | `usr/lib/systemd/system` | Rescue Mode |
| `runlevel2.target` | `usr/lib/systemd/system` | Multi-User System |
| `runlevel3.target` | `usr/lib/systemd/system` | Multi-User System |
| `runlevel4.target` | `usr/lib/systemd/system` | Multi-User System |
| `runlevel5.target` | `usr/lib/systemd/system` | Graphical Interface |
| `runlevel6.target` | `usr/lib/systemd/system` | System Reboot |
| `rygel.service` | `usr/lib/systemd/user` | Rygel DLNA/UPnP server |
| `samba-bgqd.service` | `usr/lib/systemd/system` | Samba Background Queue Daemon for printing-related jobs |
| `sbd.service` | `usr/lib/systemd/system` | Shared-storage based fencing daemon |
| `sbd_remote.service` | `usr/lib/systemd/system` | Shared-storage based fencing daemon on pacemaker remote node |
| `selinux-autorelabel-mark.service` | `usr/lib/systemd/system` | Mark the need to relabel after reboot |
| `selinux-autorelabel.service` | `usr/lib/systemd/system` | Relabel all filesystems |
| `selinux-autorelabel.target` | `usr/lib/systemd/system` | Relabel all filesystems and reboot |
| `selinux-check-proper-disable.service` | `usr/lib/systemd/system` | Check that SELinux is not disabled the unsafe way |
| `serial-getty@.service` | `usr/lib/systemd/system` | Serial Getty on %I |
| `setroubleshootd.service` | `usr/lib/systemd/system` | SETroubleshoot daemon for processing new SELinux denial logs |
| `shutdown.target` | `usr/lib/systemd/system` | System Shutdown |
| `shutdown.target` | `usr/lib/systemd/user` | Shutdown |
| `sigpwr.target` | `usr/lib/systemd/system` | Power Failure |
| `sleep.target` | `usr/lib/systemd/system` | Sleep |
| `slices.target` | `usr/lib/systemd/system` | Slice Units |
| `smartcard.target` | `usr/lib/systemd/system` | Smart Card |
| `smartcard.target` | `usr/lib/systemd/user` | Smart Card |
| `smartd.service` | `usr/lib/systemd/system` | Self Monitoring and Reporting Technology (SMART) Daemon |
| `smb.service` | `usr/lib/systemd/system` | Samba SMB Daemon |
| `sockets.target` | `usr/lib/systemd/system` | Socket Units |
| `sockets.target` | `usr/lib/systemd/user` | Sockets |
| `soft-reboot.target` | `usr/lib/systemd/system` | Reboot System Userspace |
| `sound.target` | `usr/lib/systemd/system` | Sound Card |
| `sound.target` | `usr/lib/systemd/user` | Sound Card |
| `spice-vdagent.service` | `usr/lib/systemd/user` | Spice guest session agent |
| `spice-vdagentd.service` | `usr/lib/systemd/system` | Agent daemon for Spice guests |
| `spice-vdagentd.socket` | `usr/lib/systemd/system` | Activation socket for spice guest agent daemon |
| `spice-webdavd.service` | `usr/lib/systemd/system` | webdav daemon for Spice guests |
| `ssh-access.target` | `usr/lib/systemd/system` | SSH Access Available |
| `ssh-agent.service` | `usr/lib/systemd/user` | OpenSSH key agent |
| `ssh-agent.socket` | `usr/lib/systemd/user` | OpenSSH key agent |
| `ssh-host-keys-migration.service` | `usr/lib/systemd/system` | Update OpenSSH host key permissions |
| `sshd-keygen.target` | `usr/lib/systemd/system` | ? |
| `sshd-keygen@.service` | `usr/lib/systemd/system` | OpenSSH %i Server Key Generation |
| `sshd.service` | `usr/lib/systemd/system` | OpenSSH server daemon |
| `sshd.socket` | `usr/lib/systemd/system` | OpenSSH Server Socket |
| `sshd@.service` | `usr/lib/systemd/system` | OpenSSH per-connection server daemon |
| `sssd-autofs.service` | `usr/lib/systemd/system` | SSSD AutoFS Service responder |
| `sssd-autofs.socket` | `usr/lib/systemd/system` | SSSD AutoFS Service responder socket |
| `sssd-ifp.service` | `usr/lib/systemd/system` | SSSD IFP Service responder |
| `sssd-kcm.service` | `usr/lib/systemd/system` | SSSD Kerberos Cache Manager |
| `sssd-kcm.socket` | `usr/lib/systemd/system` | SSSD Kerberos Cache Manager responder socket |
| `sssd-nss.service` | `usr/lib/systemd/system` | SSSD NSS Service responder |
| `sssd-nss.socket` | `usr/lib/systemd/system` | SSSD NSS Service responder socket |
| `sssd-pac.service` | `usr/lib/systemd/system` | SSSD PAC Service responder |
| `sssd-pac.socket` | `usr/lib/systemd/system` | SSSD PAC Service responder socket |
| `sssd-pam.service` | `usr/lib/systemd/system` | SSSD PAM Service responder |
| `sssd-pam.socket` | `usr/lib/systemd/system` | SSSD PAM Service responder socket |
| `sssd-ssh.service` | `usr/lib/systemd/system` | SSSD SSH Service responder |
| `sssd-ssh.socket` | `usr/lib/systemd/system` | SSSD SSH Service responder socket |
| `sssd-sudo.service` | `usr/lib/systemd/system` | SSSD Sudo Service responder |
| `sssd-sudo.socket` | `usr/lib/systemd/system` | SSSD Sudo Service responder socket |
| `sssd.service` | `usr/lib/systemd/system` | System Security Services Daemon |
| `storage-target-mode.target` | `usr/lib/systemd/system` | Storage Target Mode |
| `stratis-fstab-setup-with-network@.service` | `usr/lib/systemd/system` | Set up Stratis filesystems in /etc/fstab that require the network |
| `stratis-fstab-setup@.service` | `usr/lib/systemd/system` | Set up Stratis filesystems in /etc/fstab |
| `stratisd-min-postinitrd.service` | `usr/lib/systemd/system` | Stratis minimal daemon |
| `stratisd.service` | `usr/lib/systemd/system` | Stratis daemon |
| `suspend-then-hibernate.target` | `usr/lib/systemd/system` | Suspend; Hibernate if not used for a period of time |
| `suspend.target` | `usr/lib/systemd/system` | Suspend |
| `swap.target` | `usr/lib/systemd/system` | Swaps |
| `switcheroo-control.service` | `usr/lib/systemd/system` | Switcheroo Control Proxy service |
| `sys-fs-fuse-connections.mount` | `usr/lib/systemd/system` | FUSE Control File System |
| `sys-kernel-config.mount` | `usr/lib/systemd/system` | Kernel Configuration File System |
| `sys-kernel-debug.mount` | `usr/lib/systemd/system` | Kernel Debug File System |
| `sys-kernel-tracing.mount` | `usr/lib/systemd/system` | Kernel Trace File System |
| `sysinit.target` | `usr/lib/systemd/system` | System Initialization |
| `syslog.socket` | `usr/lib/systemd/system` | Syslog Socket |
| `system-update-cleanup.service` | `usr/lib/systemd/system` | Remove the Offline System Updates Symlink |
| `system-update-pre.target` | `usr/lib/systemd/system` | Offline System Update (Pre) |
| `system-update.target` | `usr/lib/systemd/system` | Offline System Update |
| `systemd-ask-password-console.path` | `usr/lib/systemd/system` | Dispatch Password Requests to Console Directory Watch |
| `systemd-ask-password-console.service` | `usr/lib/systemd/system` | Dispatch Password Requests to Console |
| `systemd-ask-password-plymouth.path` | `usr/lib/systemd/system` | Forward Password Requests to Plymouth Directory Watch |
| `systemd-ask-password-plymouth.service` | `usr/lib/systemd/system` | Forward Password Requests to Plymouth |
| `systemd-ask-password-wall.path` | `usr/lib/systemd/system` | Forward Password Requests to Wall Directory Watch |
| `systemd-ask-password-wall.service` | `usr/lib/systemd/system` | Forward Password Requests to Wall |
| `systemd-ask-password.socket` | `usr/lib/systemd/system` | Query the User Interactively for a Password |
| `systemd-ask-password.socket` | `usr/lib/systemd/user` | Query the User Interactively for a Password |
| `systemd-ask-password@.service` | `usr/lib/systemd/system` | Query the User Interactively for a Password |
| `systemd-ask-password@.service` | `usr/lib/systemd/user` | Query the User Interactively for a Password |
| `systemd-backlight@.service` | `usr/lib/systemd/system` | Load/Save Screen Backlight Brightness of %i |
| `systemd-battery-check.service` | `usr/lib/systemd/system` | Early Battery Level Check |
| `systemd-binfmt.service` | `usr/lib/systemd/system` | Set Up Additional Binary Formats |
| `systemd-bless-boot.service` | `usr/lib/systemd/system` | Mark the Current Boot Loader Entry as Good |
| `systemd-boot-check-no-failures.service` | `usr/lib/systemd/system` | Check if Any System Units Failed |
| `systemd-boot-clear-sysfail.service` | `usr/lib/systemd/system` | Clear SysFail Entry If The Boot Is Successful |
| `systemd-boot-random-seed.service` | `usr/lib/systemd/system` | Update Boot Loader Random Seed |
| `systemd-boot-update.service` | `usr/lib/systemd/system` | Automatic Boot Loader Update |
| `systemd-bootctl.socket` | `usr/lib/systemd/system` | Boot Entries Service Socket |
| `systemd-bootctl@.service` | `usr/lib/systemd/system` | Boot Entries Service |
| `systemd-bsod.service` | `usr/lib/systemd/system` | Display Boot-Time Emergency Messages In Full Screen |
| `systemd-confext-initrd.service` | `usr/lib/systemd/system` | Merge System Configuration Images into /etc/ |
| `systemd-confext.service` | `usr/lib/systemd/system` | Merge System Configuration Images into /etc/ |
| `systemd-coredump.socket` | `usr/lib/systemd/system` | Process Core Dump Socket |
| `systemd-coredump@.service` | `usr/lib/systemd/system` | Process Core Dump |
| `systemd-creds.socket` | `usr/lib/systemd/system` | Credential Encryption/Decryption |
| `systemd-creds@.service` | `usr/lib/systemd/system` | Credential Encryption/Decryption |
| `systemd-exit.service` | `usr/lib/systemd/system` | Exit the Container |
| `systemd-exit.service` | `usr/lib/systemd/user` | Exit the Session |
| `systemd-factory-reset-complete.service` | `usr/lib/systemd/system` | Mark the Factory Reset as Completed |
| `systemd-factory-reset-reboot.service` | `usr/lib/systemd/system` | Reboot to Execute Factory Reset |
| `systemd-factory-reset-request.service` | `usr/lib/systemd/system` | Request Factory Reset on Next Boot |
| `systemd-factory-reset.socket` | `usr/lib/systemd/system` | Factory Reset Management |
| `systemd-factory-reset@.service` | `usr/lib/systemd/system` | Factory Reset Management (Varlink) |
| `systemd-firstboot.service` | `usr/lib/systemd/system` | Initial Setup |
| `systemd-fsck-root.service` | `usr/lib/systemd/system` | File System Check on Root Device |
| `systemd-fsck@.service` | `usr/lib/systemd/system` | File System Check on %f |
| `systemd-growfs-root.service` | `usr/lib/systemd/system` | Grow Root File System |
| `systemd-growfs@.service` | `usr/lib/systemd/system` | Grow File System on %f |
| `systemd-halt.service` | `usr/lib/systemd/system` | System Halt |
| `systemd-hibernate-clear.service` | `usr/lib/systemd/system` | Clear Stale Hibernate Storage Info |
| `systemd-hibernate-resume.service` | `usr/lib/systemd/system` | Resume from hibernation |
| `systemd-hibernate.service` | `usr/lib/systemd/system` | System Hibernate |
| `systemd-homed-activate.service` | `usr/lib/systemd/system` | Home Area Activation |
| `systemd-homed-firstboot.service` | `usr/lib/systemd/system` | First Boot Home Area Wizard |
| `systemd-homed.service` | `usr/lib/systemd/system` | Home Area Manager |
| `systemd-hostnamed.service` | `usr/lib/systemd/system` | Hostname Service |
| `systemd-hostnamed.socket` | `usr/lib/systemd/system` | Hostname Service Socket |
| `systemd-hwdb-update.service` | `usr/lib/systemd/system` | Rebuild Hardware Database |
| `systemd-hybrid-sleep.service` | `usr/lib/systemd/system` | System Hybrid Suspend+Hibernate |
| `systemd-importd.service` | `usr/lib/systemd/system` | Disk Image Download Service |
| `systemd-importd.service` | `usr/lib/systemd/user` | Disk Image Download Service |
| `systemd-importd.socket` | `usr/lib/systemd/system` | Disk Image Download Service Socket |
| `systemd-importd.socket` | `usr/lib/systemd/user` | Disk Image Download Service Socket |
| `systemd-journal-catalog-update.service` | `usr/lib/systemd/system` | Rebuild Journal Catalog |
| `systemd-journal-flush.service` | `usr/lib/systemd/system` | Flush Journal to Persistent Storage |
| `systemd-journald-audit.socket` | `usr/lib/systemd/system` | Journal Audit Socket |
| `systemd-journald-dev-log.socket` | `usr/lib/systemd/system` | Journal Socket (/dev/log) |
| `systemd-journald-sync@.service` | `usr/lib/systemd/system` | Sync Journal for Namespace %i |
| `systemd-journald-varlink@.socket` | `usr/lib/systemd/system` | Journal Varlink Socket for Namespace %i |
| `systemd-journald.service` | `usr/lib/systemd/system` | Journal Service |
| `systemd-journald.socket` | `usr/lib/systemd/system` | Journal Sockets |
| `systemd-journald@.service` | `usr/lib/systemd/system` | Journal Service for Namespace %i |
| `systemd-journald@.socket` | `usr/lib/systemd/system` | Journal Sockets for Namespace %i |
| `systemd-kexec.service` | `usr/lib/systemd/system` | Reboot via kexec |
| `systemd-localed.service` | `usr/lib/systemd/system` | Locale Service |
| `systemd-logind-varlink.socket` | `usr/lib/systemd/system` | User Login Management Varlink Socket |
| `systemd-logind.service` | `usr/lib/systemd/system` | User Login Management |
| `systemd-loop@.service` | `usr/lib/systemd/system` | Attach File %f to Loopback Block Device |
| `systemd-machine-id-commit.service` | `usr/lib/systemd/system` | Save Transient machine-id to Disk |
| `systemd-machined.service` | `usr/lib/systemd/system` | Virtual Machine and Container Registration Service |
| `systemd-machined.service` | `usr/lib/systemd/user` | Virtual Machine and Container Registration Service |
| `systemd-machined.socket` | `usr/lib/systemd/system` | Virtual Machine and Container Registration Service Socket |
| `systemd-machined.socket` | `usr/lib/systemd/user` | Virtual Machine and Container Registration Service Socket |
| `systemd-modules-load.service` | `usr/lib/systemd/system` | Load Kernel Modules |
| `systemd-mountfsd.service` | `usr/lib/systemd/system` | DDI File System Mounter |
| `systemd-mountfsd.socket` | `usr/lib/systemd/system` | DDI File System Mounter Socket |
| `systemd-mute-console.socket` | `usr/lib/systemd/system` | Console Output Muting Service Socket |
| `systemd-mute-console@.service` | `usr/lib/systemd/system` | Console Output Muting Service |
| `systemd-network-generator.service` | `usr/lib/systemd/system` | Generate Network Units from Kernel Command Line |
| `systemd-nspawn@.service` | `usr/lib/systemd/system` | Container %i |
| `systemd-nspawn@.service` | `usr/lib/systemd/user` | Container %i |
| `systemd-nsresourced.service` | `usr/lib/systemd/system` | Namespace Resource Manager |
| `systemd-nsresourced.socket` | `usr/lib/systemd/system` | Namespace Resource Manager Socket |
| `systemd-oomd.service` | `usr/lib/systemd/system` | Userspace Out-Of-Memory (OOM) Killer |
| `systemd-oomd.socket` | `usr/lib/systemd/system` | Userspace Out-Of-Memory (OOM) Killer Socket |
| `systemd-pcrextend.socket` | `usr/lib/systemd/system` | TPM PCR Measurements |
| `systemd-pcrextend@.service` | `usr/lib/systemd/system` | TPM PCR Measurements |
| `systemd-pcrfs-root.service` | `usr/lib/systemd/system` | TPM PCR Root File System Measurement |
| `systemd-pcrfs@.service` | `usr/lib/systemd/system` | TPM PCR File System Measurement of %f |
| `systemd-pcrlock-file-system.service` | `usr/lib/systemd/system` | Lock File Systems to TPM PCR Policy |
| `systemd-pcrlock-firmware-code.service` | `usr/lib/systemd/system` | Lock Firmware Code to TPM PCR Policy |
| `systemd-pcrlock-firmware-config.service` | `usr/lib/systemd/system` | Lock Firmware Configuration to TPM PCR Policy |
| `systemd-pcrlock-machine-id.service` | `usr/lib/systemd/system` | Lock Machine ID to TPM PCR Policy |
| `systemd-pcrlock-make-policy.service` | `usr/lib/systemd/system` | Make TPM PCR Policy |
| `systemd-pcrlock-secureboot-authority.service` | `usr/lib/systemd/system` | Lock UEFI SecureBoot Authority to TPM PCR Policy |
| `systemd-pcrlock-secureboot-policy.service` | `usr/lib/systemd/system` | Lock UEFI SecureBoot Policy to TPM PCR Policy |
| `systemd-pcrlock.socket` | `usr/lib/systemd/system` | Make TPM PCR Policy |
| `systemd-pcrlock@.service` | `usr/lib/systemd/system` | Make TPM PCR Policy |
| `systemd-pcrmachine.service` | `usr/lib/systemd/system` | TPM PCR Machine ID Measurement |
| `systemd-pcrnvdone.service` | `usr/lib/systemd/system` | TPM PCR NvPCR Initialization Separator |
| `systemd-pcrphase-factory-reset.service` | `usr/lib/systemd/system` | TPM PCR Barrier (Factory Reset) |
| `systemd-pcrphase-initrd.service` | `usr/lib/systemd/system` | TPM PCR Barrier (initrd) |
| `systemd-pcrphase-storage-target-mode.service` | `usr/lib/systemd/system` | TPM PCR Barrier (Storage Target Mode) |
| `systemd-pcrphase-sysinit.service` | `usr/lib/systemd/system` | TPM PCR Barrier (Initialization) |
| `systemd-pcrphase.service` | `usr/lib/systemd/system` | TPM PCR Barrier (User) |
| `systemd-pcrproduct.service` | `usr/lib/systemd/system` | TPM NvPCR Product ID Measurement |
| `systemd-portabled.service` | `usr/lib/systemd/system` | Portable Service Manager |
| `systemd-poweroff.service` | `usr/lib/systemd/system` | System Power Off |
| `systemd-pstore.service` | `usr/lib/systemd/system` | Platform Persistent Storage Archival |
| `systemd-quotacheck-root.service` | `usr/lib/systemd/system` | Root File System Quota Check |
| `systemd-quotacheck@.service` | `usr/lib/systemd/system` | File System Quota Check |
| `systemd-random-seed.service` | `usr/lib/systemd/system` | Load/Save OS Random Seed |
| `systemd-reboot.service` | `usr/lib/systemd/system` | System Reboot |
| `systemd-remount-fs.service` | `usr/lib/systemd/system` | Remount Root and Kernel File Systems |
| `systemd-repart.service` | `usr/lib/systemd/system` | Repartition Root Disk |
| `systemd-repart.socket` | `usr/lib/systemd/system` | Disk Repartitioning Service Socket |
| `systemd-repart@.service` | `usr/lib/systemd/system` | Disk Repartitioning Service |
| `systemd-resolved-monitor.socket` | `usr/lib/systemd/system` | Resolve Monitor Varlink Socket |
| `systemd-resolved-varlink.socket` | `usr/lib/systemd/system` | Resolve Service Varlink Socket |
| `systemd-resolved.service` | `usr/lib/systemd/system` | Network Name Resolution |
| `systemd-rfkill.service` | `usr/lib/systemd/system` | Load/Save RF Kill Switch Status |
| `systemd-rfkill.socket` | `usr/lib/systemd/system` | Load/Save RF Kill Switch Status /dev/rfkill Watch |
| `systemd-soft-reboot.service` | `usr/lib/systemd/system` | Reboot System Userspace |
| `systemd-storagetm.service` | `usr/lib/systemd/system` | Storage Target Mode (NVMe-TCP) |
| `systemd-suspend-then-hibernate.service` | `usr/lib/systemd/system` | System Suspend then Hibernate |
| `systemd-suspend.service` | `usr/lib/systemd/system` | System Suspend |
| `systemd-sysctl.service` | `usr/lib/systemd/system` | Apply Kernel Variables |
| `systemd-sysext-initrd.service` | `usr/lib/systemd/system` | Merge System Extension Images into /usr/ and /opt/ |
| `systemd-sysext.service` | `usr/lib/systemd/system` | Merge System Extension Images into /usr/ and /opt/ |
| `systemd-sysext.socket` | `usr/lib/systemd/system` | System Extension Image Management |
| `systemd-sysext@.service` | `usr/lib/systemd/system` | System Extension Image Management |
| `systemd-sysupdate-reboot.service` | `usr/lib/systemd/system` | Reboot Automatically After System Update |
| `systemd-sysupdate-reboot.timer` | `usr/lib/systemd/system` | Reboot Automatically After System Update |
| `systemd-sysupdate.service` | `usr/lib/systemd/system` | Automatic System Update |
| `systemd-sysupdate.timer` | `usr/lib/systemd/system` | Automatic System Update |
| `systemd-sysupdated.service` | `usr/lib/systemd/system` | System Update Service |
| `systemd-sysusers.service` | `usr/lib/systemd/system` | Create System Users |
| `systemd-time-wait-sync.service` | `usr/lib/systemd/system` | Wait Until Kernel Time Synchronized |
| `systemd-timedated.service` | `usr/lib/systemd/system` | Time & Date Service |
| `systemd-timesyncd.service` | `usr/lib/systemd/system` | Network Time Synchronization |
| `systemd-tmpfiles-clean.service` | `usr/lib/systemd/system` | Cleanup of Temporary Directories |
| `systemd-tmpfiles-clean.service` | `usr/lib/systemd/user` | Cleanup of User's Temporary Files and Directories |
| `systemd-tmpfiles-clean.timer` | `usr/lib/systemd/system` | Daily Cleanup of Temporary Directories |
| `systemd-tmpfiles-clean.timer` | `usr/lib/systemd/user` | Daily Cleanup of User's Temporary Directories |
| `systemd-tmpfiles-setup-dev-early.service` | `usr/lib/systemd/system` | Create Static Device Nodes in /dev gracefully |
| `systemd-tmpfiles-setup-dev.service` | `usr/lib/systemd/system` | Create Static Device Nodes in /dev |
| `systemd-tmpfiles-setup.service` | `usr/lib/systemd/system` | Create System Files and Directories |
| `systemd-tmpfiles-setup.service` | `usr/lib/systemd/user` | Create User Files and Directories |
| `systemd-tpm2-clear.service` | `usr/lib/systemd/system` | Issue TPM Clear Request |
| `systemd-tpm2-setup-early.service` | `usr/lib/systemd/system` | Early TPM SRK Setup |
| `systemd-tpm2-setup.service` | `usr/lib/systemd/system` | TPM SRK Setup |
| `systemd-udev-load-credentials.service` | `usr/lib/systemd/system` | Load udev Rules from Credentials |
| `systemd-udev-settle.service` | `usr/lib/systemd/system` | Wait for udev To Complete Device Initialization |
| `systemd-udev-trigger.service` | `usr/lib/systemd/system` | Coldplug All udev Devices |
| `systemd-udevd-control.socket` | `usr/lib/systemd/system` | udev Control Socket |
| `systemd-udevd-kernel.socket` | `usr/lib/systemd/system` | udev Kernel Socket |
| `systemd-udevd-varlink.socket` | `usr/lib/systemd/system` | udev Varlink Socket |
| `systemd-udevd.service` | `usr/lib/systemd/system` | Rule-based Manager for Device Events and Files |
| `systemd-update-done.service` | `usr/lib/systemd/system` | Update is Completed |
| `systemd-update-utmp.service` | `usr/lib/systemd/system` | Record System Boot/Shutdown in UTMP |
| `systemd-user-sessions.service` | `usr/lib/systemd/system` | Permit User Sessions |
| `systemd-userdb-load-credentials.service` | `usr/lib/systemd/system` | Load JSON user/group Records from Credentials |
| `systemd-userdbd.service` | `usr/lib/systemd/system` | User Database Manager |
| `systemd-userdbd.socket` | `usr/lib/systemd/system` | User Database Manager Socket |
| `systemd-validatefs@.service` | `usr/lib/systemd/system` | Validate File System Mount Constraints of %f |
| `systemd-vconsole-setup.service` | `usr/lib/systemd/system` | Virtual Console Setup |
| `systemd-vmspawn@.service` | `usr/lib/systemd/system` | Virtual Machine %i |
| `systemd-vmspawn@.service` | `usr/lib/systemd/user` | Virtual Machine %i |
| `systemd-volatile-root.service` | `usr/lib/systemd/system` | Enforce Volatile Root File Systems |
| `systemd-zram-setup@.service` | `usr/lib/systemd/system` | Create swap on /dev/%i |
| `tailscale-systray.service` | `usr/lib/systemd/user` | Tailscale System Tray |
| `tailscaled.service` | `usr/lib/systemd/system` | Tailscale node agent |
| `target.service` | `usr/lib/systemd/system` | Restore LIO kernel target configuration |
| `targetclid.service` | `usr/lib/systemd/system` | Targetcli daemon |
| `targetclid.socket` | `usr/lib/systemd/system` | targetclid socket |
| `teamd@.service` | `usr/lib/systemd/system` | Team Daemon for device %I |
| `time-set.target` | `usr/lib/systemd/system` | System Time Set |
| `time-sync.target` | `usr/lib/systemd/system` | System Time Synchronized |
| `timers.target` | `usr/lib/systemd/system` | Timer Units |
| `timers.target` | `usr/lib/systemd/user` | Timers |
| `tinysparql-xdg-portal-3.service` | `usr/lib/systemd/user` | TinySPARQL portal service |
| `tmp.mount` | `usr/lib/systemd/system` | Temporary Directory /tmp |
| `tmux@.service` | `usr/lib/systemd/system` | tmux session for user %I |
| `tpm2.target` | `usr/lib/systemd/system` | Trusted Platform Module |
| `tuned-ppd.service` | `usr/lib/systemd/system` | PPD-to-TuneD API Translation Daemon |
| `tuned.service` | `usr/lib/systemd/system` | Dynamic System Tuning Daemon |
| `udisks2.service` | `usr/lib/systemd/system` | Disk Manager |
| `umount.target` | `usr/lib/systemd/system` | Unmount All Filesystems |
| `upower.service` | `usr/lib/systemd/system` | Daemon for power management |
| `usb-gadget.target` | `usr/lib/systemd/system` | Hardware activated USB gadget |
| `usb-release.service` | `usr/lib/systemd/system` | Release and Decouple Physical Boot USB Media |
| `usbguard.service` | `usr/lib/systemd/system` | USBGuard daemon |
| `usbmuxd.service` | `usr/lib/systemd/system` | Socket daemon for the usbmux protocol used by Apple devices |
| `user-dirs-update-gtk.service` | `usr/lib/systemd/user` | User folders update |
| `user-runtime-dir@.service` | `usr/lib/systemd/system` | User Runtime Directory /run/user/%i |
| `user@.service` | `usr/lib/systemd/system` | User Manager for UID %i |
| `var-home.mount` | `usr/lib/systemd/system` | CephFS mount for user home directories |
| `var-lib-containers.mount` | `usr/lib/systemd/system` | CephFS mount for Podman container storage |
| `var-lib-machines.mount` | `usr/lib/systemd/system` | Virtual Machine and Container Storage (Compatibility) |
| `var-lib-nfs-rpc_pipefs.mount` | `usr/lib/systemd/system` | RPC Pipe File System |
| `veritysetup-pre.target` | `usr/lib/systemd/system` | Local Verity Protected Volumes (Pre) |
| `veritysetup.target` | `usr/lib/systemd/system` | Local Verity Protected Volumes |
| `vgauthd.service` | `usr/lib/systemd/system` | VGAuth Service for open-vm-tools |
| `virt-guest-shutdown.target` | `usr/lib/systemd/system` | libvirt guests shutdown target |
| `virtchd-admin.socket` | `usr/lib/systemd/system` | libvirt Cloud Hypervisor daemon admin socket |
| `virtchd-ro.socket` | `usr/lib/systemd/system` | libvirt Cloud Hypervisor daemon read-only socket |
| `virtchd.service` | `usr/lib/systemd/system` | libvirt Cloud Hypervisor daemon |
| `virtchd.socket` | `usr/lib/systemd/system` | libvirt Cloud Hypervisor daemon socket |
| `virtinterfaced-admin.socket` | `usr/lib/systemd/system` | libvirt interface daemon admin socket |
| `virtinterfaced-ro.socket` | `usr/lib/systemd/system` | libvirt interface daemon read-only socket |
| `virtinterfaced.service` | `usr/lib/systemd/system` | libvirt interface daemon |
| `virtinterfaced.socket` | `usr/lib/systemd/system` | libvirt interface daemon socket |
| `virtlockd-admin.socket` | `usr/lib/systemd/system` | libvirt locking daemon admin socket |
| `virtlockd.service` | `usr/lib/systemd/system` | libvirt locking daemon |
| `virtlockd.socket` | `usr/lib/systemd/system` | libvirt locking daemon socket |
| `virtlogd-admin.socket` | `usr/lib/systemd/system` | libvirt logging daemon admin socket |
| `virtlogd.service` | `usr/lib/systemd/system` | libvirt logging daemon |
| `virtlogd.socket` | `usr/lib/systemd/system` | libvirt logging daemon socket |
| `virtlxcd-admin.socket` | `usr/lib/systemd/system` | libvirt LXC daemon admin socket |
| `virtlxcd-ro.socket` | `usr/lib/systemd/system` | libvirt LXC daemon read-only socket |
| `virtlxcd.service` | `usr/lib/systemd/system` | libvirt LXC daemon |
| `virtlxcd.socket` | `usr/lib/systemd/system` | libvirt LXC daemon socket |
| `virtnetworkd-admin.socket` | `usr/lib/systemd/system` | libvirt network daemon admin socket |
| `virtnetworkd-ro.socket` | `usr/lib/systemd/system` | libvirt network daemon read-only socket |
| `virtnetworkd.service` | `usr/lib/systemd/system` | libvirt network daemon |
| `virtnetworkd.socket` | `usr/lib/systemd/system` | libvirt network daemon socket |
| `virtnodedevd-admin.socket` | `usr/lib/systemd/system` | libvirt nodedev daemon admin socket |
| `virtnodedevd-ro.socket` | `usr/lib/systemd/system` | libvirt nodedev daemon read-only socket |
| `virtnodedevd.service` | `usr/lib/systemd/system` | libvirt nodedev daemon |
| `virtnodedevd.socket` | `usr/lib/systemd/system` | libvirt nodedev daemon socket |
| `virtnwfilterd-admin.socket` | `usr/lib/systemd/system` | libvirt nwfilter daemon admin socket |
| `virtnwfilterd-ro.socket` | `usr/lib/systemd/system` | libvirt nwfilter daemon read-only socket |
| `virtnwfilterd.service` | `usr/lib/systemd/system` | libvirt nwfilter daemon |
| `virtnwfilterd.socket` | `usr/lib/systemd/system` | libvirt nwfilter daemon socket |
| `virtproxyd-admin.socket` | `usr/lib/systemd/system` | libvirt proxy daemon admin socket |
| `virtproxyd-ro.socket` | `usr/lib/systemd/system` | libvirt proxy daemon read-only socket |
| `virtproxyd-tcp.socket` | `usr/lib/systemd/system` | libvirt proxy daemon non-TLS IP socket |
| `virtproxyd-tls.socket` | `usr/lib/systemd/system` | libvirt proxy daemon TLS IP socket |
| `virtproxyd.service` | `usr/lib/systemd/system` | libvirt proxy daemon |
| `virtproxyd.socket` | `usr/lib/systemd/system` | libvirt proxy daemon socket |
| `virtqemud-admin.socket` | `usr/lib/systemd/system` | libvirt QEMU daemon admin socket |
| `virtqemud-ro.socket` | `usr/lib/systemd/system` | libvirt QEMU daemon read-only socket |
| `virtqemud.service` | `usr/lib/systemd/system` | libvirt QEMU daemon |
| `virtqemud.socket` | `usr/lib/systemd/system` | libvirt QEMU daemon socket |
| `virtsecretd-admin.socket` | `usr/lib/systemd/system` | libvirt secret daemon admin socket |
| `virtsecretd-ro.socket` | `usr/lib/systemd/system` | libvirt secret daemon read-only socket |
| `virtsecretd.service` | `usr/lib/systemd/system` | libvirt secret daemon |
| `virtsecretd.socket` | `usr/lib/systemd/system` | libvirt secret daemon socket |
| `virtstoraged-admin.socket` | `usr/lib/systemd/system` | libvirt storage daemon admin socket |
| `virtstoraged-ro.socket` | `usr/lib/systemd/system` | libvirt storage daemon read-only socket |
| `virtstoraged.service` | `usr/lib/systemd/system` | libvirt storage daemon |
| `virtstoraged.socket` | `usr/lib/systemd/system` | libvirt storage daemon socket |
| `virtvboxd-admin.socket` | `usr/lib/systemd/system` | libvirt VirtualBox daemon admin socket |
| `virtvboxd-ro.socket` | `usr/lib/systemd/system` | libvirt VirtualBox daemon read-only socket |
| `virtvboxd.service` | `usr/lib/systemd/system` | libvirt VirtualBox daemon |
| `virtvboxd.socket` | `usr/lib/systemd/system` | libvirt VirtualBox daemon socket |
| `virtxend-admin.socket` | `usr/lib/systemd/system` | libvirt Xen daemon admin socket |
| `virtxend-ro.socket` | `usr/lib/systemd/system` | libvirt Xen daemon read-only socket |
| `virtxend.service` | `usr/lib/systemd/system` | libvirt Xen daemon |
| `virtxend.socket` | `usr/lib/systemd/system` | libvirt Xen daemon socket |
| `vmtoolsd.service` | `usr/lib/systemd/system` | Service for virtual machines hosted on VMware |
| `waydroid-container.service` | `usr/lib/systemd/system` | Waydroid Container |
| `winbind.service` | `usr/lib/systemd/system` | Samba Winbind Daemon |
| `wireplumber.service` | `usr/lib/systemd/user` | Multimedia Service Session Manager |
| `wireplumber@.service` | `usr/lib/systemd/user` | Multimedia Service Session Manager (%i) |
| `wpa_supplicant.service` | `usr/lib/systemd/system` | WPA supplicant |
| `wsdd.service` | `usr/lib/systemd/system` | Web Services Dynamic Discovery host daemon |
| `xdg-desktop-autostart.target` | `usr/lib/systemd/user` | Startup of XDG autostart applications |
| `xdg-desktop-portal-gnome.service` | `usr/lib/systemd/user` | Portal service (GNOME implementation) |
| `xdg-desktop-portal-gtk.service` | `usr/lib/systemd/user` | Portal service (GTK/GNOME implementation) |
| `xdg-desktop-portal-rewrite-launchers.service` | `usr/lib/systemd/user` | Rewrite dynamic launcher portal entries |
| `xdg-desktop-portal.service` | `usr/lib/systemd/user` | Portal service |
| `xdg-document-portal.service` | `usr/lib/systemd/user` | flatpak document portal service |
| `xdg-permission-store.service` | `usr/lib/systemd/user` | sandboxed app permission store |
| `xdg-user-dirs.service` | `usr/lib/systemd/user` | User folders update |
| `xfs_healer@.service` | `usr/lib/systemd/system` | Self Healing of XFS Metadata for %f |
| `xfs_healer_start.service` | `usr/lib/systemd/system` | Start Self Healing of XFS Metadata |
| `zvbid.service` | `usr/lib/systemd/system` | Proxy Sharing V4L VBI Device Between Applications |

<!-- derived from tracked unit files (1087 unit(s)) -->
<!-- /MIOS-GEN:units -->
