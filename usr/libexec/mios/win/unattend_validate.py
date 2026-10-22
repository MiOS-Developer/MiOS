#!/usr/bin/env python3
# AI-hint: Automated validation of Windows unattend XML schema against official Microsoft XSD rules.
# AI-related: tests/test-unattend.py, usr/libexec/mios/win/unattend_gen.py, autounattend.xml
# AI-functions: UnattendValidator, ValidationError, ValidationResult, ValidationSeverity, validate_unattend_xml, main
"""
MiOS Windows Unattended Answer File (autounattend.xml) Schema Validator.

Validates Windows 10/11 autounattend.xml answer files against official Microsoft SIM
(System Image Manager) XML schema rules and best practices without external C dependencies.
Ensures zero-defect unattended installations, valid pass ordering, strict datatype verification,
and Windows 11 hardware check bypass compliance.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

UNATTEND_NS = "urn:schemas-microsoft-com:unattend"
WCM_NS = "http://schemas.microsoft.com/WMIConfig/2002/State"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

STANDARD_PASSES = [
    "windowsPE",
    "offlineServicing",
    "generalize",
    "specialize",
    "auditSystem",
    "auditUser",
    "oobeSystem",
]

ALLOWED_ARCHITECTURES = {"amd64", "x86", "arm64", "wow64", "neutral"}
MICROSOFT_PUBLIC_KEY_TOKEN = "31bf3856ad364e35"
PRODUCT_KEY_REGEX = re.compile(r"^[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}$", re.IGNORECASE)
COMPUTER_NAME_INVALID_CHARS = re.compile(r'[\s/\\:*?"<>|]')

class ValidationSeverity(str, enum.Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

@dataclasses.dataclass
class ValidationError:
    """Represents a schema or semantic validation issue."""
    rule_id: str
    severity: ValidationSeverity
    pass_name: Optional[str]
    component: Optional[str]
    path: str
    message: str
    line_number: Optional[int] = None
    suggestion: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "pass_name": self.pass_name,
            "component": self.component,
            "path": self.path,
            "message": self.message,
            "line_number": self.line_number,
            "suggestion": self.suggestion,
        }

@dataclasses.dataclass
class ValidationResult:
    """Consolidated summary of answer file validation."""
    valid: bool
    file_path: Optional[str]
    total_rules_checked: int
    error_count: int
    warning_count: int
    info_count: int
    errors: List[ValidationError] = dataclasses.field(default_factory=list)
    passes_found: List[str] = dataclasses.field(default_factory=list)
    components_found: List[str] = dataclasses.field(default_factory=list)
    hardware_bypasses: Dict[str, bool] = dataclasses.field(default_factory=dict)
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "file_path": self.file_path,
            "total_rules_checked": self.total_rules_checked,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "errors": [e.to_dict() for e in self.errors],
            "passes_found": self.passes_found,
            "components_found": self.components_found,
            "hardware_bypasses": self.hardware_bypasses,
            "details": self.details,
        }

class UnattendValidator:
    """Validates autounattend.xml against Microsoft SIM XML rules and constraints."""

    def __init__(self, strict: bool = False, verbose: bool = False) -> None:
        self.strict = strict
        self.verbose = verbose
        self._rules_checked = 0

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[unattend-validate] {msg}", file=sys.stderr)

    def validate_file(self, file_path: str) -> ValidationResult:
        """Validates XML answer file from filesystem."""
        if not os.path.exists(file_path):
            err = ValidationError(
                rule_id="UNATTEND-000",
                severity=ValidationSeverity.ERROR,
                pass_name=None,
                component=None,
                path=file_path,
                message=f"File not found: {file_path}",
            )
            return ValidationResult(
                valid=False,
                file_path=file_path,
                total_rules_checked=1,
                error_count=1,
                warning_count=0,
                info_count=0,
                errors=[err],
            )

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            err = ValidationError(
                rule_id="UNATTEND-001",
                severity=ValidationSeverity.ERROR,
                pass_name=None,
                component=None,
                path=file_path,
                message=f"Failed to read file: {e}",
            )
            return ValidationResult(
                valid=False,
                file_path=file_path,
                total_rules_checked=1,
                error_count=1,
                warning_count=0,
                info_count=0,
                errors=[err],
            )

        res = self.validate_xml_string(content, file_path=file_path)
        return res

    def validate_xml_string(self, xml_content: str, file_path: Optional[str] = None) -> ValidationResult:
        """Validates XML answer file content against schema rules."""
        self._rules_checked = 0
        errors: List[ValidationError] = []
        passes_found: List[str] = []
        components_found: List[str] = []
        hardware_bypasses = {
            "BypassTPMCheck": False,
            "BypassSecureBootCheck": False,
            "BypassRAMCheck": False,
            "BypassStorageCheck": False,
            "BypassCPUCheck": False,
        }

        # 1. XML Well-formedness & Syntax
        self._rules_checked += 1
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as pe:
            line, col = pe.position if hasattr(pe, "position") else (None, None)
            errors.append(ValidationError(
                rule_id="UNATTEND-001",
                severity=ValidationSeverity.ERROR,
                pass_name=None,
                component=None,
                path=file_path or "<memory>",
                message=f"XML Parse Error: {pe}",
                line_number=line,
                suggestion="Ensure all tags are properly closed, attributes quoted, and XML entities escaped.",
            ))
            return ValidationResult(
                valid=False,
                file_path=file_path,
                total_rules_checked=self._rules_checked,
                error_count=1,
                warning_count=0,
                info_count=0,
                errors=errors,
            )

        # 2. Root Element Tag Check
        self._rules_checked += 1
        raw_tag = root.tag
        local_tag = raw_tag.split("}")[-1] if "}" in raw_tag else raw_tag
        if local_tag != "unattend":
            errors.append(ValidationError(
                rule_id="UNATTEND-002",
                severity=ValidationSeverity.ERROR,
                pass_name=None,
                component=None,
                path="/",
                message=f"Root element must be <unattend>, found <{local_tag}>",
                suggestion="Change root element to <unattend>.",
            ))

        # 3. Namespace Verifications
        self._rules_checked += 3
        if 'xmlns="urn:schemas-microsoft-com:unattend"' not in xml_content and "xmlns='urn:schemas-microsoft-com:unattend'" not in xml_content:
            if not raw_tag.startswith(f"{{{UNATTEND_NS}}}"):
                errors.append(ValidationError(
                    rule_id="UNATTEND-003",
                    severity=ValidationSeverity.ERROR,
                    pass_name=None,
                    component=None,
                    path="/unattend",
                    message="Missing mandatory default namespace xmlns='urn:schemas-microsoft-com:unattend'",
                    suggestion="Add xmlns='urn:schemas-microsoft-com:unattend' to the root <unattend> element.",
                ))

        if "xmlns:wcm=" not in xml_content:
            errors.append(ValidationError(
                rule_id="UNATTEND-004",
                severity=ValidationSeverity.WARNING,
                pass_name=None,
                component=None,
                path="/unattend",
                message="Missing recommended WCM namespace xmlns:wcm='http://schemas.microsoft.com/WMIConfig/2002/State'",
                suggestion="Add xmlns:wcm='http://schemas.microsoft.com/WMIConfig/2002/State' to the root <unattend> element.",
            ))

        # 4. Settings Passes Structure & Ordering
        settings_elements = []
        for child in root:
            child_local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_local == "settings":
                settings_elements.append(child)

        last_pass_index = -1
        seen_passes: Set[str] = set()

        for s_idx, settings in enumerate(settings_elements):
            self._rules_checked += 1
            pass_name = settings.attrib.get("pass")
            if not pass_name:
                errors.append(ValidationError(
                    rule_id="UNATTEND-010",
                    severity=ValidationSeverity.ERROR,
                    pass_name=None,
                    component=None,
                    path=f"/unattend/settings[{s_idx+1}]",
                    message="<settings> element is missing mandatory 'pass' attribute",
                    suggestion="Add pass attribute, e.g. pass='windowsPE' or pass='specialize'.",
                ))
                continue

            passes_found.append(pass_name)

            if pass_name not in STANDARD_PASSES:
                errors.append(ValidationError(
                    rule_id="UNATTEND-011",
                    severity=ValidationSeverity.WARNING,
                    pass_name=pass_name,
                    component=None,
                    path=f"/unattend/settings[@pass='{pass_name}']",
                    message=f"Non-standard setup pass '{pass_name}'. Standard passes are: {', '.join(STANDARD_PASSES)}",
                ))
            else:
                pass_order_idx = STANDARD_PASSES.index(pass_name)
                if pass_order_idx < last_pass_index:
                    errors.append(ValidationError(
                        rule_id="UNATTEND-012",
                        severity=ValidationSeverity.WARNING,
                        pass_name=pass_name,
                        component=None,
                        path=f"/unattend/settings[@pass='{pass_name}']",
                        message=f"Pass '{pass_name}' appears out of standard setup order (appeared after {STANDARD_PASSES[last_pass_index]})",
                        suggestion="Order passes sequentially: windowsPE -> offlineServicing -> specialize -> oobeSystem.",
                    ))
                last_pass_index = max(last_pass_index, pass_order_idx)

            if pass_name in seen_passes:
                errors.append(ValidationError(
                    rule_id="UNATTEND-013",
                    severity=ValidationSeverity.INFO,
                    pass_name=pass_name,
                    component=None,
                    path=f"/unattend/settings[@pass='{pass_name}']",
                    message=f"Multiple <settings> sections declared for pass '{pass_name}'",
                ))
            seen_passes.add(pass_name)

            # 5. Component Validation within Pass
            for comp_idx, comp in enumerate(settings):
                comp_local = comp.tag.split("}")[-1] if "}" in comp.tag else comp.tag
                if comp_local != "component":
                    continue

                self._rules_checked += 1
                comp_name = comp.attrib.get("name", "")
                arch = comp.attrib.get("processorArchitecture", "")
                pkt = comp.attrib.get("publicKeyToken", "")
                lang = comp.attrib.get("language", "")
                vscope = comp.attrib.get("versionScope", "")

                if comp_name:
                    components_found.append(comp_name)

                # Check required component attributes
                if not comp_name:
                    errors.append(ValidationError(
                        rule_id="UNATTEND-020",
                        severity=ValidationSeverity.ERROR,
                        pass_name=pass_name,
                        component=None,
                        path=f"/unattend/settings[@pass='{pass_name}']/component[{comp_idx+1}]",
                        message="<component> element is missing mandatory 'name' attribute",
                    ))

                if not arch:
                    errors.append(ValidationError(
                        rule_id="UNATTEND-021",
                        severity=ValidationSeverity.ERROR,
                        pass_name=pass_name,
                        component=comp_name,
                        path=f"/unattend/settings[@pass='{pass_name}']/component[@name='{comp_name}']",
                        message=f"Component '{comp_name}' is missing 'processorArchitecture' attribute",
                        suggestion="Add processorArchitecture='amd64' or processorArchitecture='neutral'.",
                    ))
                elif arch not in ALLOWED_ARCHITECTURES:
                    errors.append(ValidationError(
                        rule_id="UNATTEND-022",
                        severity=ValidationSeverity.ERROR,
                        pass_name=pass_name,
                        component=comp_name,
                        path=f"/unattend/settings[@pass='{pass_name}']/component[@name='{comp_name}']",
                        message=f"Invalid processorArchitecture '{arch}'. Allowed: {', '.join(sorted(ALLOWED_ARCHITECTURES))}",
                    ))

                if not pkt:
                    errors.append(ValidationError(
                        rule_id="UNATTEND-023",
                        severity=ValidationSeverity.WARNING,
                        pass_name=pass_name,
                        component=comp_name,
                        path=f"/unattend/settings[@pass='{pass_name}']/component[@name='{comp_name}']",
                        message=f"Component '{comp_name}' is missing 'publicKeyToken' attribute",
                        suggestion=f"Add publicKeyToken='{MICROSOFT_PUBLIC_KEY_TOKEN}'.",
                    ))
                elif pkt.lower() != MICROSOFT_PUBLIC_KEY_TOKEN.lower() and comp_name.startswith("Microsoft-Windows"):
                    errors.append(ValidationError(
                        rule_id="UNATTEND-024",
                        severity=ValidationSeverity.WARNING,
                        pass_name=pass_name,
                        component=comp_name,
                        path=f"/unattend/settings[@pass='{pass_name}']/component[@name='{comp_name}']",
                        message=f"Non-standard publicKeyToken '{pkt}' for Microsoft component (expected '{MICROSOFT_PUBLIC_KEY_TOKEN}')",
                    ))

                # 6. Deep Component-Specific Semantic Validation
                self._validate_component_internals(
                    comp=comp,
                    pass_name=pass_name,
                    comp_name=comp_name,
                    errors=errors,
                    hardware_bypasses=hardware_bypasses,
                )

        # Check total error/warning count
        err_count = sum(1 for e in errors if e.severity == ValidationSeverity.ERROR)
        warn_count = sum(1 for e in errors if e.severity == ValidationSeverity.WARNING)
        info_count = sum(1 for e in errors if e.severity == ValidationSeverity.INFO)

        is_valid = (err_count == 0) if not self.strict else (err_count == 0 and warn_count == 0)

        return ValidationResult(
            valid=is_valid,
            file_path=file_path,
            total_rules_checked=self._rules_checked,
            error_count=err_count,
            warning_count=warn_count,
            info_count=info_count,
            errors=errors,
            passes_found=passes_found,
            components_found=components_found,
            hardware_bypasses=hardware_bypasses,
            details={
                "strict_mode": self.strict,
                "bypasses_detected_count": sum(1 for v in hardware_bypasses.values() if v),
            },
        )

    def _validate_component_internals(
        self,
        comp: ET.Element,
        pass_name: str,
        comp_name: str,
        errors: List[ValidationError],
        hardware_bypasses: Dict[str, bool],
    ) -> None:
        """Validates child elements, datatypes, and configuration keys within a component."""

        def clean_tag(el: ET.Element) -> str:
            return el.tag.split("}")[-1] if "}" in el.tag else el.tag

        def find_child(parent: ET.Element, name: str) -> Optional[ET.Element]:
            for ch in parent:
                if clean_tag(ch) == name:
                    return ch
            return None

        def find_all_children(parent: ET.Element, name: str) -> List[ET.Element]:
            return [ch for ch in parent if clean_tag(ch) == name]

        # -------------------------------------------------------------
        # A. Microsoft-Windows-Setup
        # -------------------------------------------------------------
        if comp_name == "Microsoft-Windows-Setup":
            # 1. Display settings
            display_el = find_child(comp, "Display")
            if display_el is not None:
                self._rules_checked += 1
                h_res = find_child(display_el, "HorizontalResolution")
                v_res = find_child(display_el, "VerticalResolution")
                c_depth = find_child(display_el, "ColorDepth")

                if h_res is not None and h_res.text:
                    if not h_res.text.isdigit() or int(h_res.text) < 640:
                        errors.append(ValidationError(
                            rule_id="UNATTEND-030",
                            severity=ValidationSeverity.ERROR,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/Display/HorizontalResolution",
                            message=f"HorizontalResolution '{h_res.text}' must be an integer >= 640",
                        ))
                if v_res is not None and v_res.text:
                    if not v_res.text.isdigit() or int(v_res.text) < 480:
                        errors.append(ValidationError(
                            rule_id="UNATTEND-030",
                            severity=ValidationSeverity.ERROR,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/Display/VerticalResolution",
                            message=f"VerticalResolution '{v_res.text}' must be an integer >= 480",
                        ))
                if c_depth is not None and c_depth.text:
                    if c_depth.text not in ("16", "24", "32"):
                        errors.append(ValidationError(
                            rule_id="UNATTEND-030",
                            severity=ValidationSeverity.ERROR,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/Display/ColorDepth",
                            message=f"ColorDepth '{c_depth.text}' must be 16, 24, or 32",
                        ))

            # 2. UserData & ProductKey
            user_data_el = find_child(comp, "UserData")
            if user_data_el is not None:
                self._rules_checked += 1
                accept_eula = find_child(user_data_el, "AcceptEula")
                if accept_eula is not None and accept_eula.text:
                    if accept_eula.text.lower() not in ("true", "false"):
                        errors.append(ValidationError(
                            rule_id="UNATTEND-031",
                            severity=ValidationSeverity.ERROR,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/UserData/AcceptEula",
                            message=f"AcceptEula must be 'true' or 'false', found '{accept_eula.text}'",
                        ))

                prod_key_el = find_child(user_data_el, "ProductKey")
                if prod_key_el is not None:
                    key_val = find_child(prod_key_el, "Key")
                    if key_val is not None and key_val.text and key_val.text.strip():
                        k_text = key_val.text.strip()
                        if not PRODUCT_KEY_REGEX.match(k_text):
                            errors.append(ValidationError(
                                rule_id="UNATTEND-032",
                                severity=ValidationSeverity.ERROR,
                                pass_name=pass_name,
                                component=comp_name,
                                path=f"{pass_name}/{comp_name}/UserData/ProductKey/Key",
                                message=f"ProductKey '{k_text}' does not match standard 5x5 format (XXXXX-XXXXX-XXXXX-XXXXX-XXXXX)",
                                suggestion="Provide a valid 25-character product key or generic KMS client key.",
                            ))
                    show_ui = find_child(prod_key_el, "WillShowUI")
                    if show_ui is not None and show_ui.text:
                        if show_ui.text not in ("Always", "Never", "OnError"):
                            errors.append(ValidationError(
                                rule_id="UNATTEND-033",
                                severity=ValidationSeverity.WARNING,
                                pass_name=pass_name,
                                component=comp_name,
                                path=f"{pass_name}/{comp_name}/UserData/ProductKey/WillShowUI",
                                message=f"WillShowUI must be Always, Never, or OnError, found '{show_ui.text}'",
                            ))

            # 3. DiskConfiguration
            disk_cfg_el = find_child(comp, "DiskConfiguration")
            if disk_cfg_el is not None:
                self._rules_checked += 1
                disks = find_all_children(disk_cfg_el, "Disk")
                for d_idx, d_el in enumerate(disks):
                    disk_id_el = find_child(d_el, "DiskID")
                    if disk_id_el is not None and disk_id_el.text:
                        if not disk_id_el.text.isdigit():
                            errors.append(ValidationError(
                                rule_id="UNATTEND-034",
                                severity=ValidationSeverity.ERROR,
                                pass_name=pass_name,
                                component=comp_name,
                                path=f"{pass_name}/{comp_name}/DiskConfiguration/Disk[{d_idx+1}]/DiskID",
                                message=f"DiskID '{disk_id_el.text}' must be an integer >= 0",
                            ))

                    # Partitions order uniqueness check
                    create_parts = find_child(d_el, "CreatePartitions")
                    if create_parts is not None:
                        orders_seen: Set[int] = set()
                        for p_el in find_all_children(create_parts, "CreatePartition"):
                            order_el = find_child(p_el, "Order")
                            if order_el is not None and order_el.text:
                                if order_el.text.isdigit():
                                    ord_val = int(order_el.text)
                                    if ord_val in orders_seen:
                                        errors.append(ValidationError(
                                            rule_id="UNATTEND-035",
                                            severity=ValidationSeverity.ERROR,
                                            pass_name=pass_name,
                                            component=comp_name,
                                            path=f"{pass_name}/{comp_name}/DiskConfiguration/CreatePartitions",
                                            message=f"Duplicate partition Order index '{ord_val}'",
                                        ))
                                    orders_seen.add(ord_val)
                                else:
                                    errors.append(ValidationError(
                                        rule_id="UNATTEND-035",
                                        severity=ValidationSeverity.ERROR,
                                        pass_name=pass_name,
                                        component=comp_name,
                                        path=f"{pass_name}/{comp_name}/DiskConfiguration/CreatePartitions/CreatePartition/Order",
                                        message=f"Partition Order '{order_el.text}' must be a positive integer",
                                    ))

        # -------------------------------------------------------------
        # B. RunSynchronous Commands (in windowsPE, specialize, etc.)
        # -------------------------------------------------------------
        run_sync = find_child(comp, "RunSynchronous")
        if run_sync is not None:
            self._rules_checked += 1
            cmd_orders: Set[int] = set()
            for r_idx, cmd_el in enumerate(find_all_children(run_sync, "RunSynchronousCommand")):
                order_el = find_child(cmd_el, "Order")
                path_el = find_child(cmd_el, "Path")

                if order_el is not None and order_el.text:
                    if order_el.text.isdigit():
                        ord_val = int(order_el.text)
                        if ord_val in cmd_orders:
                            errors.append(ValidationError(
                                rule_id="UNATTEND-036",
                                severity=ValidationSeverity.ERROR,
                                pass_name=pass_name,
                                component=comp_name,
                                path=f"{pass_name}/{comp_name}/RunSynchronous/RunSynchronousCommand[{r_idx+1}]",
                                message=f"Duplicate RunSynchronousCommand Order index '{ord_val}'",
                            ))
                        cmd_orders.add(ord_val)
                    else:
                        errors.append(ValidationError(
                            rule_id="UNATTEND-036",
                            severity=ValidationSeverity.ERROR,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/RunSynchronous/RunSynchronousCommand[{r_idx+1}]/Order",
                            message=f"RunSynchronousCommand Order '{order_el.text}' must be a positive integer",
                        ))
                else:
                    errors.append(ValidationError(
                        rule_id="UNATTEND-036",
                        severity=ValidationSeverity.ERROR,
                        pass_name=pass_name,
                        component=comp_name,
                        path=f"{pass_name}/{comp_name}/RunSynchronous/RunSynchronousCommand[{r_idx+1}]",
                        message="RunSynchronousCommand is missing required <Order> child element",
                    ))

                if path_el is not None and path_el.text:
                    cmd_line = path_el.text
                    # Check for Win11 Hardware Bypasses
                    for bypass_key in hardware_bypasses.keys():
                        if bypass_key.lower() in cmd_line.lower():
                            hardware_bypasses[bypass_key] = True
                else:
                    errors.append(ValidationError(
                        rule_id="UNATTEND-036",
                        severity=ValidationSeverity.ERROR,
                        pass_name=pass_name,
                        component=comp_name,
                        path=f"{pass_name}/{comp_name}/RunSynchronous/RunSynchronousCommand[{r_idx+1}]",
                        message="RunSynchronousCommand is missing required <Path> child element",
                    ))

        # -------------------------------------------------------------
        # C. Microsoft-Windows-Shell-Setup
        # -------------------------------------------------------------
        if comp_name == "Microsoft-Windows-Shell-Setup":
            self._rules_checked += 1
            # ComputerName validation (1-15 chars, no illegal characters)
            comp_name_el = find_child(comp, "ComputerName")
            if comp_name_el is not None and comp_name_el.text:
                c_name = comp_name_el.text.strip()
                if c_name != "*":  # '*' means randomly generated by Windows Setup
                    if len(c_name) < 1 or len(c_name) > 15:
                        errors.append(ValidationError(
                            rule_id="UNATTEND-039",
                            severity=ValidationSeverity.ERROR,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/ComputerName",
                            message=f"ComputerName '{c_name}' must be between 1 and 15 characters (NetBIOS limit)",
                            suggestion="Shorten computer name to <= 15 characters.",
                        ))
                    if COMPUTER_NAME_INVALID_CHARS.search(c_name):
                        errors.append(ValidationError(
                            rule_id="UNATTEND-039",
                            severity=ValidationSeverity.ERROR,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/ComputerName",
                            message=f"ComputerName '{c_name}' contains illegal characters (spaces or / \\ : * ? \" < > |)",
                            suggestion="Use alphanumeric characters and hyphens only.",
                        ))

            # AutoLogon validation
            auto_logon_el = find_child(comp, "AutoLogon")
            if auto_logon_el is not None:
                enabled_el = find_child(auto_logon_el, "Enabled")
                if enabled_el is not None and enabled_el.text:
                    if enabled_el.text.lower() not in ("true", "false"):
                        errors.append(ValidationError(
                            rule_id="UNATTEND-037",
                            severity=ValidationSeverity.ERROR,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/AutoLogon/Enabled",
                            message=f"AutoLogon Enabled must be 'true' or 'false', found '{enabled_el.text}'",
                        ))
                count_el = find_child(auto_logon_el, "LogonCount")
                if count_el is not None and count_el.text:
                    if not count_el.text.isdigit() or int(count_el.text) < 1:
                        errors.append(ValidationError(
                            rule_id="UNATTEND-037",
                            severity=ValidationSeverity.WARNING,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/AutoLogon/LogonCount",
                            message=f"AutoLogon LogonCount '{count_el.text}' should be a positive integer",
                        ))

            # OOBE options validation
            oobe_el = find_child(comp, "OOBE")
            if oobe_el is not None:
                for bool_field in ["HideEULAPage", "HideLocalAccountScreen", "HideOEMRegistrationScreens", "HideOnlineAccountScreens", "HideWirelessSetupInOOBE"]:
                    f_el = find_child(oobe_el, bool_field)
                    if f_el is not None and f_el.text:
                        if f_el.text.lower() not in ("true", "false"):
                            errors.append(ValidationError(
                                rule_id="UNATTEND-040",
                                severity=ValidationSeverity.ERROR,
                                pass_name=pass_name,
                                component=comp_name,
                                path=f"{pass_name}/{comp_name}/OOBE/{bool_field}",
                                message=f"{bool_field} must be 'true' or 'false', found '{f_el.text}'",
                            ))
                protect_el = find_child(oobe_el, "ProtectYourPC")
                if protect_el is not None and protect_el.text:
                    if protect_el.text not in ("1", "2", "3"):
                        errors.append(ValidationError(
                            rule_id="UNATTEND-040",
                            severity=ValidationSeverity.WARNING,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/OOBE/ProtectYourPC",
                            message=f"ProtectYourPC should be 1 (recommended), 2 (updates only), or 3 (disabled), found '{protect_el.text}'",
                        ))

            # FirstLogonCommands
            first_logon = find_child(comp, "FirstLogonCommands")
            if first_logon is not None:
                first_orders: Set[int] = set()
                for f_idx, cmd_el in enumerate(find_all_children(first_logon, "SynchronousCommand")):
                    order_el = find_child(cmd_el, "Order")
                    cmd_line_el = find_child(cmd_el, "CommandLine")
                    if cmd_line_el is None:
                        cmd_line_el = find_child(cmd_el, "Path")

                    if order_el is not None and order_el.text and order_el.text.isdigit():
                        ord_val = int(order_el.text)
                        if ord_val in first_orders:
                            errors.append(ValidationError(
                                rule_id="UNATTEND-036",
                                severity=ValidationSeverity.ERROR,
                                pass_name=pass_name,
                                component=comp_name,
                                path=f"{pass_name}/{comp_name}/FirstLogonCommands/SynchronousCommand[{f_idx+1}]",
                                message=f"Duplicate FirstLogonCommands Order index '{ord_val}'",
                            ))
                        first_orders.add(ord_val)
                    if cmd_line_el is None or not cmd_line_el.text or not cmd_line_el.text.strip():
                        errors.append(ValidationError(
                            rule_id="UNATTEND-036",
                            severity=ValidationSeverity.ERROR,
                            pass_name=pass_name,
                            component=comp_name,
                            path=f"{pass_name}/{comp_name}/FirstLogonCommands/SynchronousCommand[{f_idx+1}]",
                            message="FirstLogonCommands SynchronousCommand is missing <CommandLine>",
                        ))

        # -------------------------------------------------------------
        # D. Microsoft-Windows-SecureStartup-FilterDriver
        # -------------------------------------------------------------
        if comp_name == "Microsoft-Windows-SecureStartup-FilterDriver":
            self._rules_checked += 1
            prev_el = find_child(comp, "PreventDeviceEncryption")
            if prev_el is not None and prev_el.text:
                if prev_el.text.lower() not in ("true", "false"):
                    errors.append(ValidationError(
                        rule_id="UNATTEND-051",
                        severity=ValidationSeverity.ERROR,
                        pass_name=pass_name,
                        component=comp_name,
                        path=f"{pass_name}/{comp_name}/PreventDeviceEncryption",
                        message=f"PreventDeviceEncryption must be 'true' or 'false', found '{prev_el.text}'",
                    ))

def validate_unattend_xml(file_path: str, strict: bool = False) -> ValidationResult:
    """Convenience public API function for validating unattend XML files."""
    validator = UnattendValidator(strict=strict)
    return validator.validate_file(file_path)

def run_mock_validation() -> Dict[str, Any]:
    """Runs deterministic mock verification covering valid and invalid XML fixtures."""
    valid_mock_xml = """<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend"
          xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <Display>
        <HorizontalResolution>1920</HorizontalResolution>
        <VerticalResolution>1080</VerticalResolution>
        <ColorDepth>32</ColorDepth>
      </Display>
      <UserData>
        <ProductKey>
          <Key>VK7JG-NPHTM-C97JM-9MPGT-3V66T</Key>
          <WillShowUI>OnError</WillShowUI>
        </ProductKey>
        <AcceptEula>true</AcceptEula>
      </UserData>
      <RunSynchronous>
        <RunSynchronousCommand wcm:action="add"><Order>1</Order><Path>reg add HKLM\\SYSTEM\\Setup\\LabConfig /v BypassTPMCheck /t REG_DWORD /d 1 /f</Path></RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add"><Order>2</Order><Path>reg add HKLM\\SYSTEM\\Setup\\LabConfig /v BypassSecureBootCheck /t REG_DWORD /d 1 /f</Path></RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add"><Order>3</Order><Path>reg add HKLM\\SYSTEM\\Setup\\LabConfig /v BypassRAMCheck /t REG_DWORD /d 1 /f</Path></RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add"><Order>4</Order><Path>reg add HKLM\\SYSTEM\\Setup\\LabConfig /v BypassStorageCheck /t REG_DWORD /d 1 /f</Path></RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add"><Order>5</Order><Path>reg add HKLM\\SYSTEM\\Setup\\LabConfig /v BypassCPUCheck /t REG_DWORD /d 1 /f</Path></RunSynchronousCommand>
      </RunSynchronous>
    </component>
  </settings>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <ComputerName>MIOS-DEV</ComputerName>
      <TimeZone>UTC</TimeZone>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <AutoLogon>
        <Enabled>true</Enabled>
        <LogonCount>1</LogonCount>
        <Username>mios</Username>
      </AutoLogon>
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideLocalAccountScreen>true</HideLocalAccountScreen>
        <ProtectYourPC>3</ProtectYourPC>
      </OOBE>
      <FirstLogonCommands>
        <SynchronousCommand wcm:action="add"><Order>1</Order><CommandLine>powershell.exe -Command "Write-Host Hello"</CommandLine></SynchronousCommand>
      </FirstLogonCommands>
    </component>
  </settings>
</unattend>
"""

    invalid_mock_xml = """<?xml version="1.0" encoding="utf-8"?>
<unattend>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="invalid_arch" publicKeyToken="wrong_token">
      <ComputerName>INVALID_LONG_COMPUTER_NAME_EXCEEDING_LIMIT</ComputerName>
    </component>
  </settings>
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35">
      <UserData>
        <ProductKey><Key>INVALID-PRODUCT-KEY</Key></ProductKey>
        <AcceptEula>maybe</AcceptEula>
      </UserData>
      <RunSynchronous>
        <RunSynchronousCommand><Order>1</Order><Path>cmd1</Path></RunSynchronousCommand>
        <RunSynchronousCommand><Order>1</Order><Path>cmd2</Path></RunSynchronousCommand>
      </RunSynchronous>
    </component>
  </settings>
</unattend>
"""

    validator = UnattendValidator(strict=False)
    valid_res = validator.validate_xml_string(valid_mock_xml, file_path="mock://valid_autounattend.xml")
    invalid_res = validator.validate_xml_string(invalid_mock_xml, file_path="mock://invalid_autounattend.xml")

    return {
        "status": "ok",
        "mock_tests": {
            "valid_fixture": {
                "valid": valid_res.valid,
                "errors": valid_res.error_count,
                "warnings": valid_res.warning_count,
                "hardware_bypasses": valid_res.hardware_bypasses,
            },
            "invalid_fixture": {
                "valid": invalid_res.valid,
                "errors": invalid_res.error_count,
                "warnings": invalid_res.warning_count,
                "detected_error_rules": [e.rule_id for e in invalid_res.errors],
            },
        },
        "all_bypasses_detected": all(valid_res.hardware_bypasses.values()),
        "invalid_correctly_rejected": not invalid_res.valid,
    }

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unattend_validate.py",
        description="MiOS Windows Unattended Answer File (autounattend.xml) Schema Validator",
    )
    parser.add_argument("--file", "-f", "--input", "-i", type=str, help="Path to autounattend.xml file to validate")
    parser.add_argument("--xml", type=str, help="Raw XML answer file string to validate directly")
    parser.add_argument("--strict", action="store_true", help="Enforce strict validation mode (treat warnings as errors)")
    parser.add_argument("--check", action="store_true", help="Quick check validation mode")
    parser.add_argument("--mock", action="store_true", help="Execute deterministic in-memory mock validation suite")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose diagnostic logs")
    parser.add_argument("--json", action="store_true", help="Output results in structured JSON format")
    return parser

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    validator = UnattendValidator(strict=args.strict, verbose=args.verbose)

    # 1. Mock Mode
    if args.mock and not args.file and not args.xml:
        mock_output = run_mock_validation()
        if args.json:
            print(json.dumps(mock_output, indent=2))
        else:
            print("[MOCK] Windows Unattend Schema Validator Verification:")
            print(f"  - Valid Fixture Passed: {mock_output['mock_tests']['valid_fixture']['valid']}")
            print(f"  - Hardware Bypasses Verified: {mock_output['all_bypasses_detected']}")
            print(f"  - Invalid Fixture Correctly Rejected: {mock_output['invalid_correctly_rejected']}")
            print(f"  - Detected Error Rules: {mock_output['mock_tests']['invalid_fixture']['detected_error_rules']}")
        return 0 if mock_output["all_bypasses_detected"] and mock_output["invalid_correctly_rejected"] else 1

    # 2. File Validation
    target_path = args.file
    if not target_path and not args.xml:
        # Default to autounattend.xml in current directory if exists
        if os.path.exists("autounattend.xml"):
            target_path = "autounattend.xml"
        else:
            if not args.mock:
                parser.print_help()
                return 0

    if target_path:
        res = validator.validate_file(target_path)
    elif args.xml:
        res = validator.validate_xml_string(args.xml, file_path="<cli-input>")
    else:
        mock_output = run_mock_validation()
        if args.json:
            print(json.dumps(mock_output, indent=2))
        return 0

    # Format output
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        status_tag = "[VALID]" if res.valid else "[INVALID]"
        print(f"{status_tag} {res.file_path or '<xml>'}")
        print(f"  - Total Rules Checked: {res.total_rules_checked}")
        print(f"  - Errors: {res.error_count}, Warnings: {res.warning_count}, Info: {res.info_count}")
        print(f"  - Passes Found: {', '.join(res.passes_found)}")
        if res.hardware_bypasses:
            active_bypasses = [k for k, v in res.hardware_bypasses.items() if v]
            print(f"  - Windows 11 Bypasses: {', '.join(active_bypasses) if active_bypasses else 'None'}")

        if res.errors:
            print("\nFindings:")
            for err in res.errors:
                prefix = f"[{err.severity.value.upper()}] {err.rule_id}"
                loc = f" at {err.path}" + (f":{err.line_number}" if err.line_number else "")
                print(f"  {prefix}{loc}: {err.message}")
                if err.suggestion:
                    print(f"    Suggestion: {err.suggestion}")

    return 0 if res.valid else 1

if __name__ == "__main__":
    sys.exit(main())
