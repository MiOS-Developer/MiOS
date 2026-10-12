# AI-hint: Dumps the real UI Automation control-view tree of the foreground window, a process window, or the desktop root as compact JSON.
# AI-related: mios-launch, mios-oscontrol-server.ps1

[CmdletBinding()]
param(
    [switch]$ForegroundWindow = $true,
    [int]$ProcessId = 0,
    [int]$MaxDepth = 15
)

Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
Add-Type -AssemblyName UIAutomationTypes -ErrorAction Stop
Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue

$Win32Sig = @"
using System;
using System.Runtime.InteropServices;
public class UIADumpW32 {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@
if (-not ([System.Management.Automation.PSTypeName]'UIADumpW32').Type) {
    Add-Type -TypeDefinition $Win32Sig -ErrorAction SilentlyContinue
}

function Get-UIATree {
    param(
        [System.Windows.Automation.AutomationElement]$Element,
        [int]$Depth = 0,
        [int]$MaxDepth = 15
    )
    if ($Depth -gt $MaxDepth) { return $null }
    if ($null -eq $Element) { return $null }

    $node = [ordered]@{
        Name = ''
        ControlType = ''
        AutomationId = ''
        Rect = $null
    }

    try { $node.Name = [string]$Element.Current.Name } catch {}
    try { $node.ControlType = [string]$Element.Current.ControlType.ProgrammaticName.Replace('ControlType.', '') } catch {}
    try { $node.AutomationId = [string]$Element.Current.AutomationId } catch {}
    $isOffscreen = $false
    try { $isOffscreen = [bool]$Element.Current.IsOffscreen } catch {}

    try {
        $rect = $Element.Current.BoundingRectangle
        if (-not $rect.IsEmpty) {
            $node.Rect = @{ X = [int]$rect.X; Y = [int]$rect.Y; W = [int]$rect.Width; H = [int]$rect.Height }
        }
    } catch {}

    # Skip invisible elements to reduce token bloat for the LLM
    if ($isOffscreen -and $Depth -gt 0) { return $null }

    $children = New-Object System.Collections.ArrayList
    try {
        # ControlViewWalker skips purely decorative elements and layouts
        $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
        $child = $walker.GetFirstChild($Element)
        while ($null -ne $child) {
            $childNode = Get-UIATree -Element $child -Depth ($Depth + 1) -MaxDepth $MaxDepth
            if ($null -ne $childNode) {
                [void]$children.Add($childNode)
            }
            $child = $walker.GetNextSibling($child)
        }
    } catch {}

    if ($children.Count -gt 0) {
        $node.Children = @($children)
    }

    # Prune uninteresting leaf nodes (no children, no name, and not an interactive type)
    $interactive = @('Button', 'Edit', 'Document', 'MenuItem', 'ListItem', 'TabItem', 'Hyperlink', 'CheckBox', 'RadioButton', 'ComboBox', 'TreeItem')
    if ($children.Count -eq 0 -and $node.ControlType -notin $interactive -and -not $node.Name) {
        return $null
    }

    return $node
}

$root = $null
if ($ProcessId -gt 0) {
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction Stop
        if ($proc.MainWindowHandle -ne 0) {
            $root = [System.Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle)
        }
    } catch {
        Write-Error "Could not find window for PID $ProcessId"
        exit 1
    }
} elseif ($ForegroundWindow) {
    $hwnd = [UIADumpW32]::GetForegroundWindow()
    if ($hwnd -ne [IntPtr]::Zero) {
        $root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
    }
} else {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
}

if ($null -eq $root) {
    Write-Error "Failed to acquire root AutomationElement."
    exit 1
}

$tree = Get-UIATree -Element $root -MaxDepth $MaxDepth
if ($null -ne $tree) {
    $tree | ConvertTo-Json -Depth 20 -Compress | Write-Output
}
