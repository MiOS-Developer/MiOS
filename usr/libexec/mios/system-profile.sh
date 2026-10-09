#!/bin/bash
# AI-hint: Interactive Profiler Menu AI-related: mios-build-assessment AI-functions: show_banner, show_menu, show_help, run_quick_summary, run_iommu_analyze...
# AI-doc: usr/share/doc/mios/manual/mios.md

set -euo pipefail

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly MAGENTA='\033[0;35m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -n "${SUDO_USER:-}" ]; then
    REAL_USER="$SUDO_USER"
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_USER="${USER:-$(whoami)}"
    REAL_HOME="${HOME:-$(getent passwd "$REAL_USER" | cut -d: -f6)}"
fi

readonly OUTPUT_DIR="$REAL_HOME/profiler-output"

QUICK_SUMMARY_DONE=false
IOMMU_DONE=false
FULL_PROFILE_DONE=false
LATEST_PROFILE=""

show_banner() {
    clear
    echo -e "${BOLD}${CYAN}"
    cat << 'EOF'
+***************************************************************+
|           INTERACTIVE SYSTEM PROFILER MENU                    |
|              Choose Your Tools                                |
+***************************************************************+
EOF
    echo -e "${NC}\n"
}

show_menu() {
    echo -e "${BOLD}${CYAN}****************************************************************${NC}"
    echo -e "${BOLD}                    MAIN MENU${NC}"
    echo -e "${BOLD}${CYAN}****************************************************************${NC}\n"

    if [ "$QUICK_SUMMARY_DONE" = true ]; then
        echo -e "  ${GREEN}[OK]${NC} 1) Quick System Summary        ${CYAN}[COMPLETED]${NC}"
    else
        echo -e "    1) Quick System Summary        ${YELLOW}[~30 seconds]${NC}"
    fi

    if [ "$IOMMU_DONE" = true ]; then
        echo -e "  ${GREEN}[OK]${NC} 2) IOMMU Group Analyzer        ${CYAN}[COMPLETED]${NC}"
    else
        echo -e "    2) IOMMU Group Analyzer        ${YELLOW}[~1-2 minutes]${NC}"
    fi

    if [ "$FULL_PROFILE_DONE" = true ]; then
        echo -e "  ${GREEN}[OK]${NC} 3) Full System Profiler        ${CYAN}[COMPLETED]${NC}"
    else
        echo -e "    3) Full System Profiler        ${YELLOW}[~3-5 minutes]${NC}"
    fi

    echo ""
    echo -e "    4) ${BOLD}Run All Tools Consecutively${NC}  ${YELLOW}[~5-8 minutes]${NC}"
    echo ""
    echo -e "${BOLD}${MAGENTA}*** Results & Comparison ***${NC}"
    echo -e "    5) View Latest Results"
    echo -e "    6) Compare Two Profiles"
    echo -e "    7) Open Output Directory"
    echo ""
    echo -e "${BOLD}${BLUE}*** Help & Info ***${NC}"
    echo -e "    h) Help - Tool Descriptions"
    echo -e "    s) System Status Check"
    echo ""
    echo -e "    q) Quit"
    echo ""
    echo -e "${CYAN}----------------------------------------------------------------${NC}"
}

show_help() {
    clear
    show_banner
    echo -e "${BOLD}${YELLOW}TOOL DESCRIPTIONS${NC}\n"

    echo -e "${BOLD}${CYAN}1) Quick System Summary${NC}"
    echo "   Fast 30-second health check of your system"
    echo "   Shows: CPU, RAM, GPU, virtualization status, IOMMU availability"
    echo "   Use when: Quick compatibility check, daily monitoring"
    echo ""

    echo -e "${BOLD}${CYAN}2) IOMMU Group Analyzer${NC}"
    echo "   Detailed PCIe/IOMMU group visualization"
    echo "   Shows: All IOMMU groups, GPU isolation, passthrough readiness"
    echo "   Use when: Planning GPU passthrough, checking PCIe topology"
    echo ""

    echo -e "${BOLD}${CYAN}3) Full System Profiler${NC}"
    echo "   System documentation"
    echo "   Collects: hardware, drivers, packages, BIOS, sensors"
    echo "   Use when: pre-installation baseline, troubleshooting, documentation"
    echo ""

    echo -e "${BOLD}${CYAN}4) Run All Tools${NC}"
    echo "   Executes all three tools in sequence with summary report"
    echo "   Creates timestamped directory with all results"
    echo "   Use when: Complete system assessment needed"
    echo ""

    echo -e "${BOLD}${CYAN}6) Compare Profiles${NC}"
    echo "   Side-by-side comparison of two system profiles"
    echo "   Shows: What changed between two points in time"
    echo "   Use when: Troubleshooting issues, tracking changes after updates"
    echo ""

    read -p "Press Enter to return to menu..." -r
}

run_quick_summary() {
    clear
    show_banner
    echo -e "${BOLD}${YELLOW}Running Quick System Summary...${NC}\n"

    mkdir -p "$OUTPUT_DIR"

    if [ -n "${SUDO_USER:-}" ]; then
        chown "$SUDO_USER:$(id -gn "$SUDO_USER")" "$OUTPUT_DIR"
    fi

    if [ -f "$SCRIPT_DIR/quick-summary.sh" ]; then
        "$SCRIPT_DIR/quick-summary.sh" | tee "$OUTPUT_DIR/quick-summary-$(date +%Y%m%d_%H%M%S).txt"
        QUICK_SUMMARY_DONE=true
        echo ""
        echo -e "${GREEN}[OK] Complete!${NC} Saved to: $OUTPUT_DIR"
    else
        echo -e "${RED}[ERR] Error: quick-summary.sh not found!${NC}"
    fi

    echo ""
    read -p "Press Enter to return to menu..." -r
}

run_iommu_analyzer() {
    clear
    show_banner
    echo -e "${BOLD}${YELLOW}Running IOMMU Group Analyzer...${NC}\n"

    mkdir -p "$OUTPUT_DIR"

    if [ -n "${SUDO_USER:-}" ]; then
        chown "$SUDO_USER:$(id -gn "$SUDO_USER")" "$OUTPUT_DIR"
    fi

    if [ -f "$SCRIPT_DIR/iommu-visualizer.sh" ]; then
        "$SCRIPT_DIR/iommu-visualizer.sh" --no-menu 2>&1 | tee "$OUTPUT_DIR/iommu-analysis-$(date +%Y%m%d_%H%M%S).txt"
        IOMMU_DONE=true
        echo ""
        echo -e "${GREEN}[OK] Complete!${NC} Saved to: $OUTPUT_DIR"
    else
        echo -e "${RED}[ERR] Error: iommu-visualizer.sh not found!${NC}"
    fi

    echo ""
    read -p "Press Enter to return to menu..." -r
}

run_full_profiler() {
    clear
    show_banner
    echo -e "${BOLD}${YELLOW}Running Full System Profiler...${NC}"
    echo -e "${YELLOW}This will take 3-5 minutes...${NC}\n"

    if [ -f "$SCRIPT_DIR/system-profiler.sh" ]; then
        "$SCRIPT_DIR/system-profiler.sh"

        LATEST_PROFILE=$(ls -t ~/system-profile/system-profile-*.txt 2>/dev/null | head -1)

        if [ -f "$LATEST_PROFILE" ]; then
            FULL_PROFILE_DONE=true
            echo ""
            echo -e "${GREEN}[OK] Complete!${NC}"
            echo -e "Profile saved to: ${CYAN}$LATEST_PROFILE${NC}"
        else
            echo -e "${RED}[ERR] Error: Profile generation failed!${NC}"
        fi
    else
        echo -e "${RED}[ERR] Error: system-profiler.sh not found!${NC}"
    fi

    echo ""
    read -p "Press Enter to return to menu..." -r
}

run_all_tools() {
    clear
    show_banner
    echo -e "${BOLD}${YELLOW}Running All Tools Consecutively...${NC}\n"

    echo "This will:"
    echo "  1. Run quick summary"
    echo "  2. Analyze IOMMU groups"
    echo "  3. Create full system profile"
    echo "  4. Generate summary report"
    echo ""
    echo "Estimated time: 5-8 minutes"
    echo ""

    read -p "Continue? (Y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        return
    fi

    if [ -f "$SCRIPT_DIR/run-all-profilers.sh" ]; then
        "$SCRIPT_DIR/run-all-profilers.sh"
        QUICK_SUMMARY_DONE=true
        IOMMU_DONE=true
        FULL_PROFILE_DONE=true
    else
        echo -e "${RED}[ERR] Error: run-all-profilers.sh not found!${NC}"
        echo ""
        echo "Running tools individually instead"
        echo ""
        run_quick_summary
        run_iommu_analyzer
        run_full_profiler
    fi

    echo ""
    read -p "Press Enter to return to menu..." -r
}

view_results() {
    clear
    show_banner
    echo -e "${BOLD}${YELLOW}Latest Results${NC}\n"

    local has_results=false

    if [ -d "$OUTPUT_DIR" ] && [ "$(ls -A $OUTPUT_DIR 2>/dev/null)" ]; then
        echo -e "${BOLD}Recent Output Files:${NC}"
        ls -lht "$OUTPUT_DIR"/*.txt 2>/dev/null | head -10 | awk '{print "  "$9" ("$5")"}'
        echo ""
        has_results=true
    fi

    if [ -d ~/system-profile ] && [ "$(ls -A ~/system-profile 2>/dev/null)" ]; then
        echo -e "${BOLD}System Profiles:${NC}"
        ls -lht ~/system-profile/system-profile-*.txt 2>/dev/null | head -5 | awk '{print "  "$9" ("$5")"}'
        echo ""
        has_results=true
    fi

    local -a assessment_dirs=()
    local assessment_dir
    for assessment_dir in "$HOME"/mios-build-assessment-*; do
        [[ -d "$assessment_dir" ]] && assessment_dirs+=("$assessment_dir")
    done
    if [[ ${#assessment_dirs[@]} -gt 0 ]]; then
        echo -e "${BOLD}Assessment Reports:${NC}"
        ls -dlt -- "${assessment_dirs[@]}" 2>/dev/null | head -5 | awk '{print "  "$9}'
        echo ""
        has_results=true
    fi

    if [ "$has_results" = false ]; then
        echo -e "${YELLOW}No results found yet.${NC}"
        echo "Run a profiler first"
    else
        echo ""
        echo -e "${CYAN}View a file:${NC}"
        echo "  less [filename]"
        echo ""
        echo -e "${CYAN}Search files:${NC}"
        echo "  grep -i 'search-term' $OUTPUT_DIR/*.txt"
    fi

    echo ""
    read -p "Press Enter to return to menu..." -r
}

compare_profiles() {
    clear
    show_banner
    echo -e "${BOLD}${YELLOW}Compare Two System Profiles${NC}\n"

    if [ -d ~/system-profile ]; then
        local -a profiles
        mapfile -t profiles < <(ls -t ~/system-profile/system-profile-*.txt 2>/dev/null)

        if [ ${#profiles[@]} -lt 2 ]; then
            echo -e "${YELLOW}Need at least 2 profiles to compare.${NC}"
            echo "Only ${#profiles[@]} profile found"
            echo ""
            echo "Run the full system profiler multiple times to create profiles"
            read -p "Press Enter to return to menu..." -r
            return
        fi

        echo -e "${BOLD}Available Profiles:${NC}\n"
        for i in "${!profiles[@]}"; do
            local date
            date=$(python3 -c "import os, sys, datetime; print(datetime.date.fromtimestamp(os.path.getmtime(sys.argv[1])))" "${profiles[$i]}" 2>/dev/null || stat -c %y "${profiles[$i]}" 2>/dev/null | cut -d' ' -f1 || stat -f "%Sm" -t "%Y-%m-%d" "${profiles[$i]}" 2>/dev/null || echo "Unknown")
            echo "  $)) $(basename "${profiles[$i]}") ($date)"
        done

        echo ""
        read -p "Select first profile (1-${#profiles[@]}): " first
        read -p "Select second profile (1-${#profiles[@]}): " second

        if [ "$first" -ge 1 ] && [ "$first" -le "${#profiles[@]}" ] && \
           [ "$second" -ge 1 ] && [ "$second" -le "${#profiles[@]}" ]; then

            echo ""
            echo -e "${BOLD}Comparing profiles...${NC}\n"

            if [ -f "$SCRIPT_DIR/profile-compare.sh" ]; then
                "$SCRIPT_DIR/profile-compare.sh" \
                    "${profiles[$((first-1))]}" \
                    "${profiles[$((second-1))]}"
            else
                echo -e "${RED}[ERR] Error: profile-compare.sh not found!${NC}"
            fi
        else
            echo -e "${RED}Invalid selection!${NC}"
        fi
    else
        echo -e "${YELLOW}No profiles found.${NC}"
        echo "Run the full system profiler first"
    fi

    echo ""
    read -p "Press Enter to return to menu..." -r
}

open_output_dir() {
    mkdir -p "$OUTPUT_DIR"

    echo ""
    echo -e "${BOLD}Output Directories:${NC}"
    echo -e "  Quick/IOMMU: ${CYAN}$OUTPUT_DIR${NC}"
    echo -e "  Full Profiles: ${CYAN}~/system-profile/${NC}"
    echo -e "  Assessments: ${CYAN}~/mios-build-assessment-*/${NC}"
    echo ""

    if command -v xdg-open >/dev/null 2>&1; then
        read -p "Open $OUTPUT_DIR in file manager? (Y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            xdg-open "$OUTPUT_DIR" 2>/dev/null &
            echo -e "${GREEN}[OK] Opened!${NC}"
        fi
    else
        echo -e "Location: ${CYAN}$OUTPUT_DIR${NC}"
    fi

    echo ""
    read -p "Press Enter to return to menu..." -r
}

check_system_status() {
    clear
    show_banner
    echo -e "${BOLD}${YELLOW}Quick System Status Check${NC}\n"

    echo -e "${BOLD}Virtualization:${NC}"
    if grep -q -E '(vmx|svm)' /proc/cpuinfo 2>/dev/null; then
        echo -e "  ${GREEN}[OK]${NC} CPU virtualization enabled"
    else
        echo -e "  ${RED}[ERR]${NC} CPU virtualization not detected"
    fi

    echo -e "\n${BOLD}IOMMU:${NC}"
    if [ -d /sys/kernel/iommu_groups ]; then
        local groups
        groups=$(ls -1 /sys/kernel/iommu_groups/ | wc -l) || true
        echo -e "  ${GREEN}[OK]${NC} IOMMU enabled ($groups groups)"
    else
        echo -e "  ${RED}[ERR]${NC} IOMMU not available"
    fi

    echo -e "\n${BOLD}KVM:${NC}"
    if [ -e /dev/kvm ]; then
        echo -e "  ${GREEN}[OK]${NC} KVM available"
    else
        echo -e "  ${RED}[ERR]${NC} KVM not available"
    fi

    echo -e "\n${BOLD}GPU:${NC}"
    local gpu_count
    gpu_count=$(lspci | grep -c -E "VGA|3D" || echo "0")
    if [ "$gpu_count" -gt 0 ]; then
        echo -e "  ${GREEN}[OK]${NC} $gpu_count GPU(s) detected"
        lspci | grep -E "VGA|3D" | while read -r line; do
            echo "    * ${line#*: }"
        done
    else
        echo -e "  ${YELLOW}[WARN]${NC} No discrete GPU detected"
    fi

    echo -e "\n${BOLD}Memory:${NC}"
    free -h | grep "^Mem:" | awk '{print "  Total: "$2", Available: "$7}'

    echo -e "\n${BOLD}Disk Space:${NC}"
    df -h / | tail -1 | awk '{print "  Root: "$4" free / "$2" total ("$5" used)"}'

    echo ""
    read -p "Press Enter to return to menu..." -r
}

main() {
    while true; do
        show_banner
        show_menu

        read -p "Select option: " choice

        case "$choice" in
            1) run_quick_summary ;;
            2) run_iommu_analyzer ;;
            3) run_full_profiler ;;
            4) run_all_tools ;;
            5) view_results ;;
            6) compare_profiles ;;
            7) open_output_dir ;;
            h|H) show_help ;;
            s|S) check_system_status ;;
            q|Q)
                echo ""
                echo -e "${CYAN}Goodbye!${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}Invalid option!${NC}"
                sleep 1
                ;;
        esac
    done
}

main "$@"
