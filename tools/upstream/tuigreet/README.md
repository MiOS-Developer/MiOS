<!-- AI-hint: Provenance and rebuild procedure for the owned upstream GNU/NSS greeter RPM; login names remain NSS account names and pretty names remain presentation metadata.
     AI-related: usr/share/mios/mios.toml [desktop.greeter.tuigreet_source], tools/patches/tuigreet-login-account.patch, tools/native/mios-gen/src/tuigreet_build.rs, automation/65-bake-hyprland.sh -->
# MiOS greeter package

Both the local fallback and remote login use `/usr/bin/tuigreet`. Upstream
tuigreet, pinned by `[desktop.greeter.tuigreet_source]`, masks the username field with the NSS GECOS display name after
account selection. Its edit handlers change the login value without removing
that old display name, making edits appear ineffective. Its remembered-session
lookup also uses the display value. The carried patch keeps the editable NSS
login visible in every prompt mode, retains pretty names in the user menu,
clears old display metadata when editing, and uses the actual login for
remembered sessions.
Account lookup, UID filtering and PAM authentication are unchanged.

The SSOT pins upstream commit
`86bf7bbc156bf38a0ca8be08804c1a4cc99cffa5`, the source archive, the exact patch,
all three changed source files and the unmodified upstream `Cargo.lock` in this
directory. The lock contains 219 registry packages with their upstream crate
checksums. A newer upstream release requires reviewing the patch and refreshing
those seals, tests and package release together; it is never silently rebased.

`mios-gen build-tuigreet --root <checkout> --out <empty-directory>` runs inside
the image builder or MiOS-DEV. `--archive <file>` accepts an already downloaded
archive but still requires its exact SSOT digest. Initial preparation downloads
locked dependencies with Cargo's checksum validation; it records every vendored
file's digest and the exact crate/checksum set. The resulting RPM and matching
source RPM rebuild and test offline from that vendor bundle. Their recipe uses
the MiOS SSOT Rust toolchain and an explicit GNU target and private target
directory. The external greeter retains GNU/NSS account-provider behavior;
MiOS's native static-binary requirements and the separate tmux/greetd audits
are unchanged.

An admitted offline builder can supply `--vendor-cache <directory>` together
with `--vendor-cache-sha256 <reviewed-inventory-sha256>`. The seal is SHA-256 of
the compact JSON inventory produced by the builder's `vendor_inventory`: outer
keys `locked_crates`, then `files`; both maps sort paths/names; file records
contain `sha256`, then `bytes`. Every crate's complete file/checksum map and
the exact lock provider/checksum set are verified before the seal is admitted.
Root's native validation mounts this independently reviewed cache read-only.
The producer still runs real `cargo vendor --locked --offline
--respect-source-config`, validates every copied source byte, and rechecks the
unchanged input seal. Cargo may re-encode `.cargo-checksum.json` or omit its
`.cargo-ok` extraction stamp; those bookkeeping changes cannot change or omit
any actual source file. The input seal, copy comparison and output inventory
remain in provenance. Missing dependencies, a stale seal or changed source
bytes refuse preparation; there is no network fallback in this mode.

The RPM tests first require both named regression cases to exist, run
each exact case, and then run the full 31-case upstream suite. A missing test
cannot yield a zero-test success. The builder also checks the real GNU loader
and dynamic dependencies, rejects embedded library search paths, and records
the actual binary and package hashes. Toolchain channel, Cargo version, source
seals and vendor inventory accompany each build; a floating stable toolchain
does not imply bit-identical binaries between different toolchain releases.

Phase 65 replaces either reviewed distro provider (`tuigreet` or
`greetd-tuigreet`) with the distinct `mios-tuigreet` RPM. That package provides
and conflicts with the stock names, so an ordinary distro update cannot
silently overwrite the fix. A pre-existing `mios-tuigreet` is explicitly
reinstalled from the newly built RPM; an unowned executable is refused.
The matching source RPM, vendor inventory and build receipt are installed under
`/usr/share/mios/sources/tuigreet`. The source RPM contains upstream source,
the patch, unchanged lock, all vendored dependencies and the build recipe,
preserving the GNU GPL v3 corresponding source with the image. The carried
patch is a derivative of the upstream GPL v3 program; it is not relicensed as
MiOS's Apache-2.0 management code.

The current raw-login-field patch passed both exact focused cases, all 31
unfiltered tests, the GNU release build and ELF audit in private Native
MiOS-DEV validation. Its binary SHA-256 is
`085e7b9b027d5688d2d21b6eef6f8e1bf8286e981cd1e492cc12b2761c7f944e`.
The unchanged lock SHA-256 is
`c533d7ccd43367daade00bdc672b02d91abc5a16e134e30cdbfe5143890b0131`.
The earlier display-masking patch passed 32 tests and produced binary
`9198b99b3b9268f7d0b12b7fd23001b2ffde916895d245e0e437a2ff0638081d`.
These leaf validations do not substitute for executing the owned RPM producer
or prove a fresh PAM login, controller routing, or full-image acceptance.
Normal image builds must produce their own source-bound receipts.
