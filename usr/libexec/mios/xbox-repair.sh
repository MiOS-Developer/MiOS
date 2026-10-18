#!/bin/bash
# AI-hint: Xbox VM Secure Boot Fix Script
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Xbox VM Secure Boot Fix Script      ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

if [ "$EUID" -eq 0 ]; then
    SUDO=""
    # The invoking user's home/name were never read; the lookup stays so a root run
    # without a resolvable SUDO_USER still stops under set -euo pipefail as before.
    getent passwd "${SUDO_USER}" | cut -d: -f6 >/dev/null
else
    SUDO="sudo"
    : "$HOME" "$USER"
fi

echo -e "${BLUE}[1/6] Checking for OVMF firmware files...${NC}"

OVMF_LOCATIONS=(
    "/usr/share/edk2/x64"
    "/usr/share/edk2-ovmf/x64"
    "/usr/share/OVMF"
    "/usr/share/qemu/ovmf-x86_64"
)

OVMF_CODE=""
OVMF_VARS=""

for location in "${OVMF_LOCATIONS[@]}"; do
    if [ -f "$location/OVMF_CODE.secboot.fd" ]; then
        OVMF_CODE="$location/OVMF_CODE.secboot.fd"
        OVMF_VARS="$location/OVMF_VARS.fd"
        echo -e "${GREEN}[ok] Found Secure Boot OVMF at: $location${NC}"
        break
    elif [ -f "$location/OVMF_CODE.secboot.4m.fd" ]; then
        OVMF_CODE="$location/OVMF_CODE.secboot.4m.fd"
        OVMF_VARS="$location/OVMF_VARS.4m.fd"
        echo -e "${GREEN}[ok] Found 4MB Secure Boot OVMF at: $location${NC}"
        break
    elif [ -f "$location/OVMF_CODE.fd" ]; then
        OVMF_CODE="$location/OVMF_CODE.fd"
        OVMF_VARS="$location/OVMF_VARS.fd"
        echo -e "${YELLOW}[!] Found standard OVMF (no Secure Boot) at: $location${NC}"
        break
    fi
done

if [ -z "$OVMF_CODE" ]; then
    echo -e "${RED}[x] No OVMF firmware found!${NC}"
    echo -e "${YELLOW}Installing edk2-ovmf package...${NC}"
    $SUDO pacman -S --noconfirm edk2-ovmf

    for location in "${OVMF_LOCATIONS[@]}"; do
        if [ -f "$location/OVMF_CODE.secboot.fd" ]; then
            OVMF_CODE="$location/OVMF_CODE.secboot.fd"
            OVMF_VARS="$location/OVMF_VARS.fd"
            break
        fi
    done

    if [ -z "$OVMF_CODE" ]; then
        echo -e "${RED}[x] Still can't find OVMF files after installation!${NC}"
        echo "Please check package installation manually"
        exit 1
    fi
fi

echo -e "  CODE: $OVMF_CODE"
echo -e "  VARS: $OVMF_VARS"

if [ ! -f "$OVMF_VARS" ]; then
    echo -e "${RED}[x] OVMF_VARS template doesn't exist: $OVMF_VARS${NC}"
    echo "Available files in directory:"
    ls -lh "$(dirname "$OVMF_VARS")"
    exit 1
fi

echo -e "\n${BLUE}[2/6] Checking VM status...${NC}"
if virsh dominfo Xbox &>/dev/null; then
    echo -e "${GREEN}[ok] VM 'Xbox' found${NC}"
    VM_STATE=$(virsh domstate Xbox 2>/dev/null)
    echo "  Current state: $VM_STATE"

    if [ "$VM_STATE" == "running" ]; then
        echo -e "${YELLOW}  Shutting down VM...${NC}"
        virsh shutdown Xbox
        sleep 5
    fi
else
    echo -e "${YELLOW}[!] VM 'Xbox' not currently defined${NC}"
fi

echo -e "\n${BLUE}[3/6] Backing up current NVRAM...${NC}"
NVRAM_PATH="/var/lib/libvirt/qemu/nvram/Xbox_VARS.fd"
if [ -f "$NVRAM_PATH" ]; then
    BACKUP_PATH="${NVRAM_PATH}.backup-$(date +%Y%m%d-%H%M%S)"
    $SUDO cp "$NVRAM_PATH" "$BACKUP_PATH"
    echo -e "${GREEN}[ok] Backed up to: $BACKUP_PATH${NC}"

    echo -e "${YELLOW}  Removing old NVRAM to get fresh Secure Boot keys...${NC}"
    $SUDO rm "$NVRAM_PATH"
else
    echo -e "${YELLOW}[!] No existing NVRAM found (first boot)${NC}"
fi

echo -e "\n${BLUE}[4/6] Creating corrected VM XML configuration...${NC}"

cat > /tmp/Xbox-fixed.xml << 'XMLEOF'
<domain type="kvm">
  <name>Xbox</name>
  <uuid>{{MIOS_VM_UUID}}</uuid>
  <metadata xmlns:libosinfo="http://libosinfo.org/xmlns/libvirt/domain/1.0" xmlns:cockpit_machines="https://github.com/cockpit-project/cockpit-machines">
    <libosinfo:libosinfo>
      <libosinfo:os id="http://microsoft.com/win/11"/>
    </libosinfo:libosinfo>
  </metadata>
  <memory unit="KiB">25165824</memory>
  <currentMemory unit="KiB">25165824</currentMemory>
  <vcpu placement="static">12</vcpu>
  <cputune>
    <vcpupin vcpu="0" cpuset="2"/>
    <vcpupin vcpu="1" cpuset="3"/>
    <vcpupin vcpu="2" cpuset="4"/>
    <vcpupin vcpu="3" cpuset="5"/>
    <vcpupin vcpu="4" cpuset="6"/>
    <vcpupin vcpu="5" cpuset="7"/>
    <vcpupin vcpu="6" cpuset="18"/>
    <vcpupin vcpu="7" cpuset="19"/>
    <vcpupin vcpu="8" cpuset="20"/>
    <vcpupin vcpu="9" cpuset="21"/>
    <vcpupin vcpu="10" cpuset="22"/>
    <vcpupin vcpu="11" cpuset="23"/>
    <emulatorpin cpuset="0-1,16-17"/>
  </cputune>
  <os>
    <type arch="x86_64" machine="pc-q35-10.1">hvm</type>
    <loader readonly="yes" secure="yes" type="pflash">OVMF_CODE_PLACEHOLDER</loader>
    <nvram template="OVMF_VARS_PLACEHOLDER">/var/lib/libvirt/qemu/nvram/Xbox_VARS.fd</nvram>
    <bootmenu enable="yes"/>
  </os>
  <features>
    <acpi/>
    <apic/>
    <hyperv mode="custom">
      <relaxed state="on"/>
      <vapic state="on"/>
      <spinlocks state="on" retries="8191"/>
      <vpindex state="on"/>
      <runtime state="on"/>
      <synic state="on"/>
      <stimer state="on"/>
      <frequencies state="on"/>
      <tlbflush state="on"/>
      <ipi state="on"/>
      <avic state="on"/>
    </hyperv>
    <smm state="on"/>
  </features>
  <cpu mode="host-passthrough" check="none" migratable="on">
    <topology sockets="1" dies="1" clusters="1" cores="6" threads="2"/>
    <cache mode="passthrough"/>
    <feature policy="require" name="topoext"/>
    <feature policy="require" name="invtsc"/>
  </cpu>
  <clock offset='utc'>
    <timer name="rtc" tickpolicy="catchup"/>
    <timer name="pit" tickpolicy="delay"/>
    <timer name="hpet" present="no"/>
    <timer name="hypervclock" present="yes"/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type="file" device="cdrom">
      <driver name="qemu" type="raw"/>
      <source file="/var/lib/libvirt/images/Win11_25H2_English_x64.iso"/>
      <target dev="sda" bus="sata"/>
      <readonly/>
      <address type="drive" controller="0" bus="0" target="0" unit="0"/>
    </disk>
    <disk type="volume" device="cdrom">
      <driver name="qemu" type="raw"/>
      <source pool="default" volume="virtio-win.iso"/>
      <target dev="sdb" bus="sata"/>
      <readonly/>
      <address type="drive" controller="0" bus="0" target="0" unit="1"/>
    </disk>
    <controller type="usb" index="0" model="qemu-xhci" ports="15">
      <address type="pci" domain="0x0000" bus="0x02" slot="0x00" function="0x0"/>
    </controller>
    <controller type="pci" index="0" model="pcie-root"/>
    <controller type="pci" index="1" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="1" port="0x10"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x02" function="0x0" multifunction="on"/>
    </controller>
    <controller type="pci" index="2" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="2" port="0x11"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x02" function="0x1"/>
    </controller>
    <controller type="pci" index="3" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="3" port="0x12"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x02" function="0x2"/>
    </controller>
    <controller type="pci" index="4" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="4" port="0x13"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x02" function="0x3"/>
    </controller>
    <controller type="pci" index="5" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="5" port="0x14"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x02" function="0x4"/>
    </controller>
    <controller type="pci" index="6" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="6" port="0x15"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x02" function="0x5"/>
    </controller>
    <controller type="pci" index="7" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="7" port="0x16"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x02" function="0x6"/>
    </controller>
    <controller type="pci" index="8" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="8" port="0x17"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x02" function="0x7"/>
    </controller>
    <controller type="pci" index="9" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="9" port="0x18"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x03" function="0x0" multifunction="on"/>
    </controller>
    <controller type="pci" index="10" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="10" port="0x19"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x03" function="0x1"/>
    </controller>
    <controller type="pci" index="11" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="11" port="0x1a"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x03" function="0x2"/>
    </controller>
    <controller type="pci" index="12" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="12" port="0x1b"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x03" function="0x3"/>
    </controller>
    <controller type="pci" index="13" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="13" port="0x1c"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x03" function="0x4"/>
    </controller>
    <controller type="pci" index="14" model="pcie-root-port">
      <model name="pcie-root-port"/>
      <target chassis="14" port="0x1d"/>
      <address type="pci" domain="0x0000" bus="0x00" slot="0x03" function="0x5"/>
    </controller>
    <controller type="sata" index="0">
      <address type="pci" domain="0x0000" bus="0x00" slot="0x1f" function="0x2"/>
    </controller>
    <interface type="network">
      <mac address="{{MIOS_VM_MAC}}"/>
      <source network="default"/>
      <model type="virtio"/>
      <address type="pci" domain="0x0000" bus="0x01" slot="0x00" function="0x0"/>
    </interface>
    <serial type="pty">
      <target type="isa-serial" port="0">
        <model name="isa-serial"/>
      </target>
    </serial>
    <console type="pty">
      <target type="serial" port="0"/>
    </console>
    <input type="tablet" bus="usb">
      <address type="usb" bus="0" port="1"/>
    </input>
    <input type="mouse" bus="ps2"/>
    <input type="keyboard" bus="ps2"/>
    <tpm model="tpm-crb">
      <backend type="emulator" version="2.0">
        <profile name="default-v1"/>
      </backend>
    </tpm>
    <graphics type="spice" port="5900" autoport="no" listen="127.0.0.1">
      <listen type="address" address="127.0.0.1"/>
    </graphics>
    <sound model="ich9">
      <address type="pci" domain="0x0000" bus="0x00" slot="0x1b" function="0x0"/>
    </sound>
    <audio id="1" type="none"/>
    <video>
      <model type="none"/>
    </video>
    <hostdev mode="subsystem" type="pci" managed="yes">
      <source>
        <address domain="0x0000" bus="0x04" slot="0x00" function="0x0"/>
      </source>
      <boot order="1"/>
      <address type="pci" domain="0x0000" bus="0x04" slot="0x00" function="0x0"/>
    </hostdev>
    <hostdev mode="subsystem" type="pci" managed="yes">
      <source>
        <address domain="0x0000" bus="0x01" slot="0x00" function="0x0"/>
      </source>
      <address type="pci" domain="0x0000" bus="0x05" slot="0x00" function="0x0"/>
    </hostdev>
    <hostdev mode="subsystem" type="pci" managed="yes">
      <source>
        <address domain="0x0000" bus="0x01" slot="0x00" function="0x1"/>
      </source>
      <address type="pci" domain="0x0000" bus="0x06" slot="0x00" function="0x0"/>
    </hostdev>
    <hostdev mode="subsystem" type="usb" managed="yes">
      <source>
        <vendor id="0x054c"/>
        <product id="0x0ce6"/>
      </source>
      <address type="usb" bus="0" port="2"/>
    </hostdev>
    <hostdev mode="subsystem" type="usb" managed="yes">
      <source>
        <vendor id="0x1532"/>
        <product id="0x009f"/>
      </source>
      <address type="usb" bus="0" port="3"/>
    </hostdev>
    <hostdev mode="subsystem" type="usb" managed="yes">
      <source>
        <vendor id="0x18d1"/>
        <product id="0x4eeb"/>
      </source>
      <address type="usb" bus="0" port="6"/>
    </hostdev>
    <hostdev mode="subsystem" type="pci" managed="yes">
      <source>
        <address domain="0x0000" bus="0x03" slot="0x00" function="0x0"/>
      </source>
      <address type="pci" domain="0x0000" bus="0x07" slot="0x00" function="0x0"/>
    </hostdev>
    <hostdev mode="subsystem" type="usb" managed="yes">
      <source>
        <vendor id="0x359b"/>
        <product id="0x0004"/>
      </source>
      <address type="usb" bus="0" port="4"/>
    </hostdev>
    <watchdog model="itco" action="reset"/>
    <memballoon model="none"/>
  </devices>
</domain>
XMLEOF

sed -i "s|OVMF_CODE_PLACEHOLDER|$OVMF_CODE|g" /tmp/Xbox-fixed.xml
sed -i "s|OVMF_VARS_PLACEHOLDER|$OVMF_VARS|g" /tmp/Xbox-fixed.xml

_UUID="${MIOS_VM_UUID:-$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "45463e80-2ca6-4467-bd36-0ed899018e17")}"
_MAC="${MIOS_VM_MAC:-52:54:00:9d:56:d9}" # Keep stable default if not provided

sed -i "s|{{MIOS_VM_UUID}}|$_UUID|g" /tmp/Xbox-fixed.xml
sed -i "s|{{MIOS_VM_MAC}}|$_MAC|g" /tmp/Xbox-fixed.xml
# Looking Glass profile from mios.toml [vm.looking_glass]: SPICE (never VNC) on the port the client
# dials, one spicevmc channel, SPICE audio, SMT topology + topoext, localtime RTC.
/usr/libexec/mios/vfio/setup-looking-glass.py --render /tmp/Xbox-fixed.xml /tmp/Xbox-fixed.xml --vcpus template

echo -e "${GREEN}[ok] XML configuration created${NC}"

echo -e "\n${BLUE}[5/6] Applying VM configuration...${NC}"
if virsh define /tmp/Xbox-fixed.xml; then
    echo -e "${GREEN}[ok] VM configuration applied successfully${NC}"
else
    echo -e "${RED}[x] Failed to apply configuration${NC}"
    echo "Configuration saved to: /tmp/Xbox-fixed.xml"
    exit 1
fi

echo -e "\n${BLUE}[6/6] Starting VM...${NC}"
if virsh start Xbox; then
    echo -e "${GREEN}[ok] VM started successfully${NC}"
else
    echo -e "${RED}[x] Failed to start VM${NC}"
    echo "Check: sudo journalctl -u libvirtd -n 50"
    exit 1
fi

echo -e "\n${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         [ok] Fix Complete!                ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

echo -e "${YELLOW}Configuration Summary:${NC}"
echo -e "  OVMF CODE: $OVMF_CODE"
echo -e "  OVMF VARS: $OVMF_VARS"
echo -e "  Secure Boot: $(grep -q 'secure="yes"' /tmp/Xbox-fixed.xml && echo 'Enabled' || echo 'Disabled')"
echo -e "  VM State: $(virsh domstate Xbox)"

echo -e "\n${YELLOW}Note:${NC} Fresh NVRAM created with Secure Boot keys enrolled."
echo -e "Windows 11 should now detect Secure Boot as properly enabled.\n"
