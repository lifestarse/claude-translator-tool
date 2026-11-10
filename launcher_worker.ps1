# Окно задачи для launcher_console.py (Windows). Запускает скрипт конвейера в
# своём окне и держит окно открытым после выхода, чтобы был виден итог.
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory,
    [Parameter(Mandatory = $true)][string]$Script,
    [string]$TaskLabel = "Задача",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ScriptArgs
)

$Host.UI.RawUI.WindowTitle = $TaskLabel
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
Set-Location -LiteralPath $WorkingDirectory

Write-Host "=== $TaskLabel ==="
Write-Host "$Python -u $Script $($ScriptArgs -join ' ')"
Write-Host ""

if ($ScriptArgs) {
    & $Python -u $Script @ScriptArgs
} else {
    & $Python -u $Script
}
$code = $LASTEXITCODE

Write-Host ""
Write-Host "=== $TaskLabel завершена, код $code ==="
Read-Host "Enter — закрыть окно" | Out-Null
exit $code
