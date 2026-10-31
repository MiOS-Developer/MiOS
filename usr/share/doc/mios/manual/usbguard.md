<!-- AI-hint: Manual pages distilled from the source comments of usbguard, sanitized, each passage anchored to the comment it came from. -->

# usbguard

### Rule set file path. The USBGuard daemon will use this file...

Rule set file path.

The USBGuard daemon will use this file to load the policy
rule set from it and to write new rules received via the
IPC interface.

RuleFile=/path/to/rules.conf

<!-- mios-src:d2b079991341 from etc/usbguard/usbguard-daemon.conf:1-9 -->

### Rule set folder path. The USBGuard daemon will use this...

Rule set folder path.

The USBGuard daemon will use this folder to load the policy
rule set from it and to write new rules received via the
IPC interface.

RuleFolder=/path/to/rulesfolder/

<!-- mios-src:a580fe3e4a74 from etc/usbguard/usbguard-daemon.conf:12-20 -->

### Implicit policy target. How to treat devices that don't...

Implicit policy target.

How to treat devices that don't match any rule in the
policy. One of:

* allow  - authorize the device
* block  - block the device
* reject - remove the device

<!-- mios-src:cdd6a93af2fd from etc/usbguard/usbguard-daemon.conf:25-34 -->

### Present device policy. How to treat devices that are...

Present device policy.

How to treat devices that are already connected when the
daemon starts. One of:

* allow        - authorize every present device
* block        - deauthorize every present device
* reject       - remove every present device
* keep         - just sync the internal state and leave it
* apply-policy - evaluate the ruleset for every present
                 device

<!-- mios-src:b643e99fa732 from etc/usbguard/usbguard-daemon.conf:37-49 -->

### Present controller policy. How to treat USB controllers...

Present controller policy.

How to treat USB controllers that are already connected
when the daemon starts. One of:

* allow        - authorize every present device
* block        - deauthorize every present device
* reject       - remove every present device
* keep         - just sync the internal state and leave it
* apply-policy - evaluate the ruleset for every present
                 device

<!-- mios-src:5be06d056f73 from etc/usbguard/usbguard-daemon.conf:52-64 -->

### Inserted device policy. How to treat USB devices that are...

Inserted device policy.

How to treat USB devices that are already connected
*after* the daemon starts. One of:

* block        - deauthorize every present device
* reject       - remove every present device
* apply-policy - evaluate the ruleset for every present
                 device

<!-- mios-src:96041204ff44 from etc/usbguard/usbguard-daemon.conf:67-77 -->

### Restore controller device state. The USBGuard daemon...

Restore controller device state.

The USBGuard daemon modifies some attributes of controller
devices like the default authorization state of new child device
instances. Using this setting, you can controll whether the
daemon will try to restore the attribute values to the state
before modificaton on shutdown.

SECURITY CONSIDERATIONS: If set to true, the USB authorization
policy could be bypassed by performing some sort of attack on the
daemon (via a local exploit or via a USB device) to make it shutdown
and restore to the operating-system default state (known to be permissive).

<!-- mios-src:10db2f9600b7 from etc/usbguard/usbguard-daemon.conf:80-93 -->

### Device manager backend Which device manager backend...

Device manager backend

Which device manager backend implementation to use. One of:

* uevent - Netlink based implementation which uses sysfs to scan for present
           devices and an uevent netlink socket for receiving USB device
           related events.
* dummy  - A dummy device manager which simulates several devices and device
           events. Useful for testing.

<!-- mios-src:b055aa9992fc from etc/usbguard/usbguard-daemon.conf:96-106 -->

### Users allowed to use the IPC interface. A space delimited...

Users allowed to use the IPC interface.

A space delimited list of usernames that the daemon will
accept IPC connections from.

IPCAllowedUsers=username1 username2 ...

<!-- mios-src:6f4f4df7d531 from etc/usbguard/usbguard-daemon.conf:115-122 -->

### Groups allowed to use the IPC interface. A space delimited...

Groups allowed to use the IPC interface.

A space delimited list of groupnames that the daemon will
accept IPC connections from.

IPCAllowedGroups=groupname1 groupname2 ...

<!-- mios-src:97c5f49201ad from etc/usbguard/usbguard-daemon.conf:125-132 -->

### IPC access control definition files path. The files at this...

IPC access control definition files path.

The files at this location will be interpreted by the daemon
as access control definition files. The (base)name of a file
should be in the form:

  [user][:<group>]

and should contain lines in the form:

  <section>=[privilege] ...

This way each file defines who is able to connect to the IPC
bus and what privileges he has.

<!-- mios-src:4254b8c0c9e8 from etc/usbguard/usbguard-daemon.conf:135-150 -->

### Generate device specific rules including the "via-port"...

Generate device specific rules including the "via-port"
attribute.

This option modifies the behavior of the allowDevice
action. When instructed to generate a permanent rule,
the action can generate a port specific rule. Because
some systems have unstable port numbering, the generated
rule might not match the device after rebooting the system.

If set to false, the generated rule will still contain
the "parent-hash" attribute which also defines an association
to the parent device. See usbguard-rules.conf(5) for more
details.

<!-- mios-src:3111c6edd3fb from etc/usbguard/usbguard-daemon.conf:153-167 -->

### USBGuard Audit events log backend One of

USBGuard Audit events log backend

One of:

* FileAudit - Log audit events into a file specified by
              AuditFilePath setting (see below)
* LinuxAudit - Log audit events using the Linux Audit
               subsystem (using audit_log_user_message)

<!-- mios-src:366957a0ba9e from etc/usbguard/usbguard-daemon.conf:170-179 -->
