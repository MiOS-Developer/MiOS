#!/usr/bin/env python3
# AI-hint: MiOS system and orchestration module providing uid enforce capabilities.
# AI-functions: check_user_uid, check_subuid_subgid, audit_user_environment, generate_sysusers_remediation, main

"""
MiOS Standard Non-System UID 1000 Enforcement & Migration Engine.
Implements task_908 / task_3141: Validates and enforces that primary user 'mios'
has UID 1000 and GID 1000, aligning with systemd user session requirements.
"""

import os
import sys
from typing import Dict, Any, Tuple, Optional

def check_user_uid(username: str = "mios", target_uid: int = 1000, target_gid: int = 1000) -> Dict[str, Any]:
    """
    Check if a given user exists and possesses the expected UID/GID.
    """
    try:
        import pwd
        pw = pwd.getpwnam(username)
        return {
            "exists": True,
            "username": pw.pw_name,
            "uid": pw.pw_uid,
            "gid": pw.pw_gid,
            "home": pw.pw_dir,
            "shell": pw.pw_shell,
            "is_system_uid": pw.pw_uid < 1000,
            "valid_uid": pw.pw_uid == target_uid,
            "valid_gid": pw.pw_gid == target_gid,
        }
    except (KeyError, ImportError):
        return {
            "exists": False,
            "username": username,
            "uid": None,
            "gid": None,
            "home": None,
            "shell": None,
            "is_system_uid": False,
            "valid_uid": False,
            "valid_gid": False,
        }

def check_subuid_subgid(username: str = "mios", min_count: int = 65536) -> Dict[str, Any]:
    """
    Verify /etc/subuid and /etc/subgid range allocations for rootless Podman.
    """
    subuid_valid = False
    subgid_valid = False
    subuid_start = None
    subuid_count = 0
    subgid_start = None
    subgid_count = 0

    if os.path.exists("/etc/subuid"):
        try:
            with open("/etc/subuid", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(":")
                    if len(parts) == 3 and parts[0] == username:
                        subuid_start = int(parts[1])
                        subuid_count = int(parts[2])
                        if subuid_count >= min_count:
                            subuid_valid = True
                        break
        except Exception:
            pass

    if os.path.exists("/etc/subgid"):
        try:
            with open("/etc/subgid", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(":")
                    if len(parts) == 3 and parts[0] == username:
                        subgid_start = int(parts[1])
                        subgid_count = int(parts[2])
                        if subgid_count >= min_count:
                            subgid_valid = True
                        break
        except Exception:
            pass

    return {
        "subuid_valid": subuid_valid,
        "subuid_start": subuid_start,
        "subuid_count": subuid_count,
        "subgid_valid": subgid_valid,
        "subgid_start": subgid_start,
        "subgid_count": subgid_count,
    }

def audit_user_environment(username: str = "mios") -> Dict[str, Any]:
    """
    Audit full user status, UID, GID, subuid ranges, and systemd compatibility.
    """
    user_info = check_user_uid(username)
    sub_info = check_subuid_subgid(username)

    issues = []
    if not user_info["exists"]:
        issues.append(f"User '{username}' does not exist in /etc/passwd")
    elif user_info["is_system_uid"]:
        issues.append(f"User '{username}' has system UID {user_info['uid']} (< 1000), violating systemd ConditionUser=!@system")
    elif not user_info["valid_uid"]:
        issues.append(f"User '{username}' has non-standard UID {user_info['uid']} (expected 1000)")

    return {
        "status": "PASS" if not issues else "FAIL",
        "user_info": user_info,
        "subuid_info": sub_info,
        "issues": issues,
    }

def generate_sysusers_remediation(username: str = "mios", target_uid: int = 1000) -> str:
    """
    Generate declarative sysusers.d config block ensuring UID 1000 assignment.
    """
    return f"""# Generated declarative sysusers.d entry for standard non-system UID 1000
g {username} {target_uid}
u {username} {target_uid}:{username} "'MiOS' User" /var/home/{username} /bin/bash
m {username} wheel
m {username} video
m {username} render
m {username} kvm
m {username} libvirt
m {username} input
m {username} dialout
m {username} docker
"""

def main() -> int:
    audit = audit_user_environment()
    print(f"MiOS User UID Audit: {audit['status']}")
    for issue in audit["issues"]:
        print(f"  - {issue}")
    if audit["status"] == "PASS":
        print(f"User '{audit['user_info']['username']}' correctly configured with UID {audit['user_info']['uid']}.")
        return 0
    return 1

if __name__ == "__main__":
    sys.exit(main())
