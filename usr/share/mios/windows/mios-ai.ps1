# AI-hint: Legacy Windows mios-ai command enters the native AI workspace in the invoking terminal.
# AI-related: usr/share/mios/windows/mios-native-client-setup.ps1, build-mios.ps1
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$nativeBin = if ($env:MIOS_NATIVE_BIN) { $env:MIOS_NATIVE_BIN } else { Join-Path $env:ProgramData 'MiOS\bin' }
$entry = Join-Path $nativeBin 'mios-native-entry.ps1'
if (-not (Test-Path -LiteralPath $entry)) { throw 'MiOS native terminal entrypoint is missing; run the MiOS native client setup.' }
& $entry ai @Arguments
