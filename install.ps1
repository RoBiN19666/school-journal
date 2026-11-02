# =============================================================================
#  Electronic attendance journal - one-command installation (Windows)
# =============================================================================
#  Run in PowerShell (from the project folder):
#      powershell -ExecutionPolicy Bypass -File .\install.ps1
#
#  What it does:
#    1. checks that Docker and docker compose are available; if Docker is
#       missing, it explains that Docker Desktop for Windows is installed by
#       hand (a large application with a graphical wizard, a paid licence for
#       large organisations and a reboot) and exits. The installer itself
#       downloads nothing and installs nothing;
#    2. creates .env from .env.example (if .env does not exist yet) with random
#       SECRET_KEY and POSTGRES_PASSWORD and working default settings;
#    3. builds and starts the containers: docker compose up -d --build;
#    4. waits until the journal answers its self-check (manage.py healthcheck);
#    5. prints the journal address and what to do on first opening.
#
#  Re-running is safe: an existing .env is not overwritten, secrets are not
#  regenerated, data in the volumes is left alone.
# =============================================================================

[CmdletBinding()]
param(
    # How many seconds to wait for the journal to become ready.
    [int]$HealthTimeout = 300,
    # Pause between readiness checks, seconds.
    [int]$HealthInterval = 4
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$EnvFile = Join-Path (Get-Location) '.env'
$EnvTemplate = Join-Path (Get-Location) '.env.example'
$Service = 'web'

# --- Output formatting -------------------------------------------------------

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "  [OK] $Text" -ForegroundColor Green
}

function Write-Note([string]$Text) {
    Write-Host "       $Text" -ForegroundColor DarkGray
}

function Write-Warn([string]$Text) {
    Write-Host "  [!] $Text" -ForegroundColor Yellow
}

function Fail([string]$Text) {
    Write-Host ''
    Write-Host "  [ERROR] $Text" -ForegroundColor Red
    Write-Host ''
    exit 1
}

# The message "Docker Desktop is installed by hand". No installation, no
# questions: this is a deliberate decision - see README, section
# "If Docker is not installed".
function Show-DockerManualMessage([string]$Missing) {
    Write-Host ''
    if ($Missing -eq 'docker') {
        Write-Warn 'Docker not found.'
        Write-Host 'Docker is required to run the journal in containers: PostgreSQL and the application itself.'
    }
    else {
        Write-Warn "Docker is installed, but there is no 'docker compose'."
        Write-Host 'The compose plugin is required to bring up the database and the application with one command.'
    }
    Write-Host @"

Docker Desktop for Windows is installed by hand - the journal installer does
not install it and downloads nothing. The reasons:
  * it is a large application with a graphical setup wizard;
  * a system reboot is required after installation;
  * the Docker Desktop licence for large organisations is paid - the decision
    to install must stay with you.

  Download page:         https://www.docker.com/products/docker-desktop/
  Installing on Windows: https://docs.docker.com/desktop/install/windows-install/

After installation start Docker Desktop, wait until it says "Engine running"
and run the journal installation again:
  powershell -ExecutionPolicy Bypass -File .\install.ps1
"@
    exit 1
}

# --- Docker ------------------------------------------------------------------

# Calling docker with predictable exit-code handling: in Windows PowerShell
# docker output on stderr must not turn into an exception.
function Invoke-Docker {
    param(
        [string[]]$Arguments,
        [switch]$Capture
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($Capture) {
            $text = (& docker @Arguments 2>&1 | Out-String)
            return [pscustomobject]@{ Code = $LASTEXITCODE; Output = $text }
        }
        & docker @Arguments
        return [pscustomobject]@{ Code = $LASTEXITCODE; Output = '' }
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

# --- The .env file -----------------------------------------------------------

function Get-EnvValue([string]$Key) {
    if (-not (Test-Path -LiteralPath $EnvFile)) { return '' }
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        if ($line.StartsWith("$Key=")) { return $line.Substring($Key.Length + 1) }
    }
    return ''
}

function Set-EnvValue([string]$Key, [string]$Value) {
    $lines = New-Object 'System.Collections.Generic.List[string]'
    if (Test-Path -LiteralPath $EnvFile) {
        foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) { $lines.Add($line) }
    }
    $replaced = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].StartsWith("$Key=")) {
            $lines[$i] = "$Key=$Value"
            $replaced = $true
            break
        }
    }
    if (-not $replaced) { $lines.Add("$Key=$Value") }
    # UTF-8 without BOM and LF line endings: docker compose reads .env line by
    # line, and a "\r" at the end of a value would end up in the database password.
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $content = ($lines -join "`n") + "`n"
    [System.IO.File]::WriteAllText($EnvFile, $content, $encoding)
}

function New-RandomString([int]$Length) {
    $alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    $bytes = New-Object 'byte[]' ($Length * 2)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $chars = New-Object 'char[]' $Length
    for ($i = 0; $i -lt $Length; $i++) {
        $chars[$i] = $alphabet[$bytes[$i] % $alphabet.Length]
    }
    return (-join $chars)
}

# --- Start -------------------------------------------------------------------

Write-Host ''
Write-Host 'Electronic attendance journal - installation' -ForegroundColor White
Write-Host "Django 5 + PostgreSQL + Docker, directory: $PSScriptRoot" -ForegroundColor DarkGray

# --- 1. Docker and docker compose --------------------------------------------

Write-Step 'Checking Docker'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Show-DockerManualMessage 'docker'
}

$info = Invoke-Docker -Arguments @('info') -Capture
if ($info.Code -ne 0) {
    Fail @"
Docker is installed, but its service is not responding.
  Start Docker Desktop and wait until it says "Engine running".
  Check it yourself: docker info
"@
}
$dockerVersion = (Invoke-Docker -Arguments @('--version') -Capture).Output.Trim()
Write-Ok "Docker: $dockerVersion"

$composeVersion = Invoke-Docker -Arguments @('compose', 'version', '--short') -Capture
if ($composeVersion.Code -ne 0) {
    Show-DockerManualMessage 'compose'
}
Write-Ok "docker compose: $($composeVersion.Output.Trim())"

# --- 2. The .env file --------------------------------------------------------

Write-Step 'Preparing settings (.env)'

if (Test-Path -LiteralPath $EnvFile) {
    Write-Ok 'The .env file already exists - leaving it as it is.'
    Write-Note 'Secrets and settings are not recreated: re-running does not change them.'
}
else {
    if (-not (Test-Path -LiteralPath $EnvTemplate)) {
        Fail "The file .env.example is missing - download the project in full (the settings template is part of it)."
    }
    Copy-Item -LiteralPath $EnvTemplate -Destination $EnvFile -Force

    $secretKey = New-RandomString 64
    $dbPassword = New-RandomString 40

    Set-EnvValue 'SECRET_KEY' $secretKey
    Set-EnvValue 'POSTGRES_PASSWORD' $dbPassword
    Set-EnvValue 'DEBUG' '0'
    Set-EnvValue 'ALLOWED_HOSTS' 'localhost,127.0.0.1'
    Set-EnvValue 'DJANGO_LANGUAGE_CODE' 'ru'

    if ($secretKey.Length -lt 50 -or $dbPassword.Length -lt 16) {
        Remove-Item -LiteralPath $EnvFile -Force -ErrorAction SilentlyContinue
        Fail 'Could not generate random values. Create .env manually from .env.example.'
    }
    Write-Ok 'The .env file has been created from .env.example.'
    Write-Note 'SECRET_KEY and POSTGRES_PASSWORD were generated randomly and are stored only in .env.'
}

# Required values: without them the containers will not come up, better to say
# it right away.
$missing = @()
if (-not (Get-EnvValue 'SECRET_KEY')) { $missing += 'SECRET_KEY' }
if (-not (Get-EnvValue 'POSTGRES_PASSWORD')) { $missing += 'POSTGRES_PASSWORD' }
if (-not (Get-EnvValue 'DEBUG')) { $missing += 'DEBUG' }
if (-not (Get-EnvValue 'ALLOWED_HOSTS')) { $missing += 'ALLOWED_HOSTS' }
if ($missing.Count -gt 0) {
    Fail @"
The .env file does not set: $($missing -join ', ')
  Open .env and fill them in (hints are in .env.example) or delete .env
  and run install.ps1 again: it will create the file with random secrets.
"@
}
$secretKey = Get-EnvValue 'SECRET_KEY'
if ($secretKey.Length -lt 50) {
    Fail @"
SECRET_KEY in .env is shorter than 50 characters - the journal will not start like that.
  Generate a new key (in PowerShell):
    -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 64 | % {[char]$_})
  Note: changing SECRET_KEY signs all users out of the journal.
"@
}
Write-Ok "Required settings are in place (DEBUG=$(Get-EnvValue 'DEBUG'), ALLOWED_HOSTS=$(Get-EnvValue 'ALLOWED_HOSTS'))."

# --- 3. Compose configuration ------------------------------------------------

Write-Step 'Checking the configuration'

$config = Invoke-Docker -Arguments @('compose', 'config', '-q') -Capture
if ($config.Code -ne 0) {
    Write-Host $config.Output
    Fail @"
docker compose rejected the configuration (message above).
  This is usually an unset or wrong variable in .env - compare it with .env.example.
"@
}
Write-Ok 'The configuration is valid.'

# --- 4. Build and start ------------------------------------------------------

Write-Step 'Building and starting containers'
Write-Note 'The first build takes a few minutes: images are downloaded and libraries are installed.'
$up = Invoke-Docker -Arguments @('compose', 'up', '-d', '--build')
if ($up.Code -ne 0) {
    Fail @"
The containers did not start.
  Look at the reason: docker compose logs --tail=100 web
  Common cases (no access to Docker Hub, port 8000 already in use) are covered in
  the README, section 'If something went wrong'.
"@
}
Write-Ok "Containers are running (db and $Service)."

# --- 5. Waiting for readiness ------------------------------------------------

Write-Step 'Waiting for the journal to become ready'
Write-Note "Self-check every $HealthInterval s, $HealthTimeout s maximum."

$started = Get-Date
$notRunning = 0
$ready = $false
while (-not $ready) {
    $probe = Invoke-Docker -Arguments @('compose', 'exec', '-T', $Service, 'python', 'manage.py', 'healthcheck') -Capture
    if ($probe.Code -eq 0) {
        $ready = $true
        break
    }

    if ($probe.Output -match 'is not running|not running') {
        $notRunning++
    }
    else {
        $notRunning = 0
    }
    if ($notRunning -ge 3) {
        Write-Host ''
        Write-Warn "Container $Service is not running - the journal will not come up."
        $null = Invoke-Docker -Arguments @('compose', 'logs', '--tail=40', $Service)
        Fail @"
The journal did not start.
  See the full logs: docker compose logs $Service
  The cause is usually visible in the first lines of the container log - the
  migration step and the start of the application.
"@
    }

    if (((Get-Date) - $started).TotalSeconds -ge $HealthTimeout) {
        Write-Host ''
        Write-Warn "The journal did not answer within $HealthTimeout s."
        $null = Invoke-Docker -Arguments @('compose', 'ps')
        $null = Invoke-Docker -Arguments @('compose', 'logs', '--tail=60', $Service)
        Fail @"
The journal did not come up within $HealthTimeout s.
  Logs: docker compose logs $Service
  If the build is still downloading images - run it again, the progress is kept.
  Another timeout: powershell -ExecutionPolicy Bypass -File .\install.ps1 -HealthTimeout 900
"@
    }

    Write-Host '.' -NoNewline
    Start-Sleep -Seconds $HealthInterval
}
Write-Host ''
Write-Ok 'The journal is answering, the self-check is clean.'

# --- 6. Summary --------------------------------------------------------------

$port = Get-EnvValue 'WEB_PORT'
if (-not ($port -match '^\d+$')) { $port = '8000' }
$url = "http://localhost:$port"

Write-Host ''
Write-Host '==============================================================================' -ForegroundColor White
Write-Host '  ELECTRONIC ATTENDANCE JOURNAL IS RUNNING' -ForegroundColor Green
Write-Host '==============================================================================' -ForegroundColor White
Write-Host @"

  Address:          $url
  Django admin:     $url/admin/
  Demo page:        $url/demo/   (appears with DEMO_MODE=1)

  FIRST OPENING - SETUP WIZARD
  Open the address in a browser: the journal will show the initial setup page
  (/setup/). There the main administrator is created: login, name and password,
  the journal name and, optionally, a logo. After that the wizard does not
  appear again, and you sign in with the created login at $url/login/.

  DEMO MODE (sign-in without a password, separate demo database)
  1. In the .env file set DEMO_MODE=1
  2. Restart: powershell -ExecutionPolicy Bypass -File .\install.ps1
  3. Open $url/demo/ and click "Sign in as the methodist"
     or "Sign in as the teacher".
  Demo data lives in a separate database and does not affect the working journal.
  Before real work set DEMO_MODE=0 back.

  DATA AND BACKUPS
  The database and the uploaded files live in the postgres_data and media_data
  volumes and are kept through updates and shutdowns. A copy of all data is the
  "Backup" section in the journal (administrator only).

  USEFUL COMMANDS (in the project directory)
    docker compose logs -f $Service          follow the logs
    docker compose ps                   container status
    docker compose down                 stop (data is kept)
    docker compose up -d --build        start after replacing files

  Settings are in the .env file, details are in README.md
  and docs/admin-guide.md.

"@
