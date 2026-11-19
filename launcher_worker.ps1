<#
Окно задачи для диспетчера (launcher_console.py) и пульта (config_ui.py).

Диспетчер открывает этот файл в новом окне PowerShell и запоминает PID окна
в .launcher_processes.json; «Остановить» — это taskkill /T по этому PID, то
есть вместе с python и всеми его агентами. Поэтому скрипт сам ничего не
отслеживает: он только запускает python в нужном каталоге и показывает вывод.

    powershell -File launcher_worker.ps1 -Python <python.exe> -WorkingDirectory <корень>
        -Script <путь к .py> -TaskLabel <подпись> [-LogFile <журнал>] -ScriptArgs <аргументы...>

-ScriptArgs обязан стоять последним: всё после него уходит скрипту как есть,
включая --count, --every и прочие флаги с дефисами.

-LogFile — дописывать вывод ещё и в файл (UTF-8 без BOM). Нужен задачам,
которые своего журнала не ведут: пульт показывает хвост publish.log.
autorun пишет autorun.log сам, ему -LogFile не передаётся — иначе строки
задвоились бы.
#>
# Без [Parameter(...)]: у «продвинутого» скрипта PowerShell сам разбирает
# хвост и спотыкается на «--count 70». Простой param() складывает всё
# нераспознанное в $args как есть — оттуда и берутся аргументы скрипта.
param(
    [string]$Python,
    [string]$WorkingDirectory,
    [string]$Script,
    [string]$TaskLabel = "задача",
    [string]$LogFile = ""
)

if (-not $Python -or -not $WorkingDirectory -or -not $Script) {
    Write-Host "launcher_worker.ps1: нужны -Python, -WorkingDirectory и -Script"
    Read-Host "Enter — закрыть окно"
    exit 2
}
$ScriptArgs = @($args | Where-Object { $_ -ne "-ScriptArgs" })

# Python пишет UTF-8 (sys.stdout.reconfigure); без этого консоль Windows
# читает его в OEM-кодировке, и кириллица в окне и в журнале — каша.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

$Host.UI.RawUI.WindowTitle = "$TaskLabel — $(Split-Path -Leaf $Script)"
Set-Location -LiteralPath $WorkingDirectory

$started = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$header = "=== $started  $TaskLabel`: python $(Split-Path -Leaf $Script) $($ScriptArgs -join ' ')"
Write-Host $header

if ($LogFile) {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::AppendAllText($LogFile, $header + "`r`n", $utf8)
    # stderr сливается в общий поток: в журнале должна быть и трассировка.
    # ErrorActionPreference=Continue, иначе первая строка stderr в PowerShell 5
    # оборвала бы конвейер как NativeCommandError.
    $ErrorActionPreference = "Continue"
    & $Python -u $Script @ScriptArgs 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        [System.IO.File]::AppendAllText($LogFile, $line + "`r`n", $utf8)
    }
} else {
    & $Python -u $Script @ScriptArgs
}
$code = $LASTEXITCODE

$finished = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$footer = "=== $finished  $TaskLabel завершена, код $code"
Write-Host ""
Write-Host $footer
if ($LogFile) {
    [System.IO.File]::AppendAllText($LogFile, $footer + "`r`n", $utf8)
}

# Окно с ошибкой не закрываем: иначе трейсбек исчезает вместе с ним. Пока
# окно открыто, диспетчер считает задачу запущенной — это честно, её итог
# ещё не прочитан. Успешное завершение закрывает окно само.
if ($code -ne 0) {
    Read-Host "Задача завершилась с ошибкой. Enter — закрыть окно"
}
exit $code
