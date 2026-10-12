// AI-hint: Compiler-backed PowerShell gates fail closed on unreadable sources and unavailable tools.
// AI-related: automation/lint-powershell.sh, automation/lint-ps-analyzer.sh
use crate::Report;
use std::path::Path;
use std::process::Command;

const SCRIPT: &str = r#"
$ErrorActionPreference = 'Stop'
try {
    $files = ConvertFrom-Json -InputObject $env:GATE_INPUTS_JSON
    $files = @($files)
    if ($files.Count -eq 0) { throw 'No PowerShell sources selected' }
    $findings = [Collections.Generic.List[string]]::new()
    if ($env:GATE_ANALYZE -eq '1') {
        Import-Module PSScriptAnalyzer -ErrorAction Stop
        if (-not (Test-Path -LiteralPath $env:GATE_SETTINGS -PathType Leaf)) {
            throw 'PSScriptAnalyzer settings are missing'
        }
    }
    foreach ($file in $files) {
        $text = [IO.File]::ReadAllText($file, [Text.Encoding]::UTF8)
        $tokens = $null
        $errors = $null
        [Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors) | Out-Null
        foreach ($parseError in $errors) {
            $findings.Add(('PARSE_ERROR: {0}:{1}:{2}: {3}' -f $file, $parseError.Extent.StartLineNumber, $parseError.Extent.StartColumnNumber, $parseError.ErrorId))
        }
        if ($env:GATE_ANALYZE -eq '1' -and $errors.Count -eq 0) {
            $results = @(Invoke-ScriptAnalyzer -Path $file -Settings $env:GATE_SETTINGS -Severity Error -ErrorAction Stop)
            foreach ($result in $results) {
                $findings.Add(('PSSA_ERROR: {0}:{1}: {2}' -f $file, $result.Line, $result.RuleName))
            }
        }
    }
    [pscustomobject]@{checked=$files.Count; findings=@($findings.ToArray())} | ConvertTo-Json -Compress
} catch {
    # Exception text may contain source snippets or operator settings.
    [Console]::Error.WriteLine('PowerShell gate could not read or analyze its inputs')
    exit 2
}
"#;

fn subjects(root: &Path) -> Result<Vec<String>, String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["ls-files", "-z", "--", "*.ps1", "*.psm1", "*.psd1"])
        .output()
        .map_err(|_| "Git source census is unavailable".to_string())?;
    if !output.status.success() {
        return Err("Git source census failed".into());
    }
    let text = std::str::from_utf8(&output.stdout).map_err(|_| "Non-UTF-8 source path")?;
    let mut files = Vec::new();
    for relative in text.split('\0').filter(|path| !path.is_empty()) {
        let path = root.join(relative);
        if !path.is_file() {
            return Err(format!(
                "Tracked PowerShell input is unreadable: {relative}"
            ));
        }
        files.push(path.to_string_lossy().into_owned());
    }
    if files.is_empty() {
        return Err("No tracked PowerShell sources; an empty census is not a pass".into());
    }
    Ok(files)
}

fn execute(root: &Path, files: &[String], analyze: bool, binary: &str) -> Result<Report, String> {
    let output = Command::new(binary)
        .args(["-NoProfile", "-NonInteractive", "-Command", SCRIPT])
        .env(
            "GATE_INPUTS_JSON",
            serde_json::to_string(files).map_err(|e| e.to_string())?,
        )
        .env("GATE_ANALYZE", if analyze { "1" } else { "0" })
        .env(
            "GATE_SETTINGS",
            root.join("automation/PSScriptAnalyzerSettings.psd1"),
        )
        .output()
        .map_err(|_| format!("Required native PowerShell interpreter unavailable: {binary}"))?;
    if !output.status.success() {
        return Err(format!(
            "PowerShell gate execution failed (exit {:?})",
            output.status.code()
        ));
    }
    let result: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "PowerShell gate returned no valid receipt".to_string())?;
    if result["checked"].as_u64() != Some(files.len() as u64) {
        return Err("PowerShell gate receipt has an incomplete source census".into());
    }
    let findings = result["findings"]
        .as_array()
        .ok_or("PowerShell gate receipt is missing findings")?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_string)
                .ok_or_else(|| "PowerShell gate receipt has an invalid finding".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Report {
        check: name(analyze).into(),
        ok: findings.is_empty(),
        could_not_run: None,
        summary: format!("{} tracked PowerShell sources checked", files.len()),
        findings,
    })
}

fn name(analyze: bool) -> &'static str {
    if analyze {
        "powershell-analyze"
    } else {
        "powershell-parse"
    }
}

pub fn check(root: &Path, analyze: bool) -> Report {
    // A Linux process must never hand ext4 paths to Windows PowerShell.
    let binary = if cfg!(windows) {
        "powershell.exe"
    } else {
        "pwsh"
    };
    subjects(root)
        .and_then(|files| execute(root, &files, analyze, binary))
        .unwrap_or_else(|error| Report {
            check: name(analyze).into(),
            ok: false,
            could_not_run: Some(error),
            summary: String::new(),
            findings: Vec::new(),
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn real_parser_rejects_syntax_and_unreadable_inputs() -> Result<(), Box<dyn std::error::Error>>
    {
        let fixture = tempfile::tempdir()?;
        let path = fixture.path().join("operator's source.ps1");
        let second = fixture.path().join("second source.ps1");
        std::fs::write(&second, "'ok'\n")?;
        let files = vec![
            path.to_string_lossy().into_owned(),
            second.to_string_lossy().into_owned(),
        ];
        let binary = if cfg!(windows) {
            "powershell.exe"
        } else {
            "pwsh"
        };
        std::fs::write(&path, "function Valid { 'ok' }\n")?;
        assert!(execute(fixture.path(), &files, false, binary)?.ok);
        std::fs::write(&path, "function Broken {\n")?;
        let broken = execute(fixture.path(), &files, false, binary)?;
        assert!(!broken.ok);
        assert!(broken
            .findings
            .iter()
            .any(|line| line.contains("PARSE_ERROR:")));
        std::fs::remove_file(&path)?;
        assert!(execute(fixture.path(), &files, false, binary).is_err());
        assert!(execute(fixture.path(), &files, false, "missing-mios-powershell").is_err());
        assert!(subjects(fixture.path()).is_err());
        Ok(())
    }

    #[test]
    fn real_analyzer_requires_settings_and_rejects_parse_errors(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let fixture = tempfile::tempdir()?;
        let path = fixture.path().join("subject.ps1");
        let files = vec![path.to_string_lossy().into_owned()];
        let binary = if cfg!(windows) {
            "powershell.exe"
        } else {
            "pwsh"
        };
        std::fs::write(&path, "'ok'\n")?;
        assert!(execute(fixture.path(), &files, true, binary).is_err());
        std::fs::create_dir(fixture.path().join("automation"))?;
        std::fs::write(
            fixture
                .path()
                .join("automation/PSScriptAnalyzerSettings.psd1"),
            "@{}\n",
        )?;
        assert!(execute(fixture.path(), &files, true, binary)?.ok);
        std::fs::write(
            &path,
            "ConvertTo-SecureString 'fixture' -AsPlainText -Force\n",
        )?;
        let finding = execute(fixture.path(), &files, true, binary)?;
        assert!(!finding.ok);
        assert!(finding.findings.iter().any(|line| {
            line.contains("PSSA_ERROR:")
                && line.contains("PSAvoidUsingConvertToSecureStringWithPlainText")
        }));
        std::fs::write(&path, "function Broken {\n")?;
        assert!(!execute(fixture.path(), &files, true, binary)?.ok);
        Ok(())
    }
}
