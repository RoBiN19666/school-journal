# =============================================================================
#  Electronic attendance journal - reset to the first-run state (Windows)
# =============================================================================
#  Run in PowerShell (from the project folder):
#      powershell -ExecutionPolicy Bypass -File .\reset.ps1
#
#  What it does:
#    1. checks that it is run from the project folder (docker-compose.yml is
#       here);
#    2. shows what will be deleted and asks for confirmation: the word DELETE
#       typed in full (not y/yes);
#    3. unless -NoBackup is given, makes a backup with
#       "python manage.py backup" and saves it into .\backups next to the
#       project; if the web container is not running, it warns and continues;
#    4. removes the containers and the volumes: docker compose down -v. This
#       deletes the journal database, the uploaded logo and favicon and the
#       demo database;
#    5. builds and starts the containers again: docker compose up -d --build.
#       The journal opens the first-run setup wizard at http://localhost:8000/.
#
#  The .env file with secrets and settings is NOT deleted. The project files
#  and the backups folder are NOT deleted either.
#
#  Usage:
#      powershell -ExecutionPolicy Bypass -File .\reset.ps1
#      powershell -ExecutionPolicy Bypass -File .\reset.ps1 -NoBackup
#      powershell -ExecutionPolicy Bypass -File .\reset.ps1 -Help
# =============================================================================

[CmdletBinding()]
param(
    # Skip the backup step and reset at once.
    [switch]$NoBackup,
    # Print the help and exit without doing anything.
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$Service = 'web'
$ComposeFile = Join-Path (Get-Location) 'docker-compose.yml'
$EnvFile = Join-Path (Get-Location) '.env'

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

# Read-only lookup of a value in .env: the file is never modified or deleted.
function Get-EnvValue([string]$Key) {
    if (-not (Test-Path -LiteralPath $EnvFile)) { return '' }
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        if ($line.StartsWith("$Key=")) { return $line.Substring($Key.Length + 1) }
    }
    return ''
}

# --- Help --------------------------------------------------------------------

if ($Help) {
    Write-Host @'
Resetting the electronic attendance journal (Django + PostgreSQL + Docker).

Usage:
    powershell -ExecutionPolicy Bypass -File .\reset.ps1
    powershell -ExecutionPolicy Bypass -File .\reset.ps1 -NoBackup
    powershell -ExecutionPolicy Bypass -File .\reset.ps1 -Help

What the script does:
    1. checks that it is run from the project folder (docker-compose.yml is
       here) and that docker and docker compose are available;
    2. shows what will be deleted and asks for confirmation: type DELETE in
       full (not y/yes);
    3. makes a backup with python manage.py backup into .\backups next to the
       project and prints its path (unless -NoBackup is given; if the web
       container is not running, the backup is skipped with a warning);
    4. removes the containers and the volumes: docker compose down -v. This
       deletes the journal database, the uploaded logo and favicon and the
       demo database;
    5. builds and starts the containers again: docker compose up -d --build.
       The journal opens the first-run setup wizard at http://localhost:8000/.

What is NOT deleted: the .env file with secrets and settings, the project
files and the backups folder.
'@
    exit 0
}

# --- Start -------------------------------------------------------------------

Write-Host ''
Write-Host 'Electronic attendance journal - reset' -ForegroundColor White
Write-Host "Django 5 + PostgreSQL + Docker, directory: $PSScriptRoot" -ForegroundColor DarkGray

# --- 1. The project folder ---------------------------------------------------

if (-not (Test-Path -LiteralPath $ComposeFile)) {
    Fail @"
There is no docker-compose.yml in the current folder: $(Get-Location)
  Run the script from the journal folder:
    cd C:\path\to\journal
    powershell -ExecutionPolicy Bypass -File .\reset.ps1
"@
}

# --- 2. Warning and confirmation ---------------------------------------------

Write-Host ''
Write-Host '==============================================================================' -ForegroundColor White
Write-Host '  WARNING: THIS DELETES THE JOURNAL DATA' -ForegroundColor Red
Write-Host '==============================================================================' -ForegroundColor White
Write-Host @"

  Will be DELETED:
    * the journal database: all students, programmes, groups, schedule,
      attendance marks, topics, users and settings stored in the database;
    * the uploaded logo and favicon (the media volume);
    * the demo database, if it exists.

  Will NOT be deleted:
    * the .env file with secrets and settings;
    * the project files and the backups folder.

  After the reset the journal starts empty and opens the first-run setup
  wizard again. Type the word DELETE in full to continue.
"@
Write-Host ''

$answer = Read-Host 'Type DELETE (in full) to continue, anything else cancels'
if ($answer -cne 'DELETE') {
    Write-Host ''
    Fail 'Reset cancelled - nothing was changed.'
}

# --- 3. Docker and docker compose --------------------------------------------

Write-Step 'Checking Docker'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail @"
Docker is not installed or not in PATH.
  Install Docker Desktop and run the reset again:
  see docs/admin-guide.md, section 2.
"@
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
    Fail @"
The 'docker compose' command (Compose v2) is not available.
  Update Docker Desktop, then run the reset again:
    docker compose version
"@
}
Write-Ok 'docker compose is available.'

# --- 4. Backup ---------------------------------------------------------------

if ($NoBackup) {
    Write-Note 'Backup skipped (-NoBackup).'
}
else {
    Write-Step 'Making a backup'
    $backupDir = Join-Path (Get-Location) 'backups'
    $backupName = "backup-$(Get-Date -Format 'yyyyMMdd-HHmmss').json"
    $containerPath = "/tmp/$backupName"
    $hostPath = Join-Path $backupDir $backupName

    $services = Invoke-Docker -Arguments @('compose', 'ps', '--status', 'running', '--services') -Capture
    $running = $false
    if ($services.Code -eq 0) {
        foreach ($line in ($services.Output -split "`r?`n")) {
            if ($line.Trim() -eq $Service) { $running = $true; break }
        }
    }

    if (-not $running) {
        Write-Warn "The $Service container is not running - continuing without a backup."
        Write-Note 'The database volume is deleted later. If the data matters, start the journal and run the reset again.'
    }
    else {
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

        $backup = Invoke-Docker -Arguments @(
            'compose', 'exec', '-T', $Service, 'python', 'manage.py', 'backup',
            '--output', $containerPath, '--quiet'
        ) -Capture
        if ($backup.Code -ne 0) {
            Fail @"
Could not make a backup. Nothing was deleted.
  Check the logs: docker compose logs --tail=100 $Service
  To reset without a backup, run:
    powershell -ExecutionPolicy Bypass -File .\reset.ps1 -NoBackup
"@
        }

        $copy = Invoke-Docker -Arguments @('compose', 'cp', "web:$containerPath", $hostPath) -Capture
        if ($copy.Code -ne 0 -or -not (Test-Path -LiteralPath $hostPath)) {
            Fail @"
Could not copy the backup to $hostPath. Nothing was deleted.
  To reset without a backup, run:
    powershell -ExecutionPolicy Bypass -File .\reset.ps1 -NoBackup
"@
        }
        if ((Get-Item -LiteralPath $hostPath).Length -eq 0) {
            Fail @"
The backup file is empty: $hostPath. Nothing was deleted.
  To reset without a backup, run:
    powershell -ExecutionPolicy Bypass -File .\reset.ps1 -NoBackup
"@
        }

        $null = Invoke-Docker -Arguments @('compose', 'exec', '-T', $Service, 'rm', '-f', $containerPath) -Capture
        Write-Ok "Backup saved: $hostPath"
    }
}

# --- 5. Remove the containers and the data volumes ---------------------------

Write-Step 'Removing the containers and the data volumes'
Write-Note 'This deletes the journal database, the uploaded logo and favicon and the demo database.'
$down = Invoke-Docker -Arguments @('compose', 'down', '-v')
if ($down.Code -ne 0) {
    Fail @"
Could not remove the containers and the volumes. Nothing else was changed.
  Check the reason: docker compose ps
"@
}
Write-Ok 'Containers and volumes removed.'

# --- 6. Build and start ------------------------------------------------------

Write-Step 'Building and starting the containers'
Write-Note 'The first build can take a few minutes: images are downloaded and libraries are installed.'
$up = Invoke-Docker -Arguments @('compose', 'up', '-d', '--build')
if ($up.Code -ne 0) {
    Fail @"
The containers did not start.
  Look at the reason: docker compose logs --tail=100 $Service
  The data volumes were already deleted; fix the cause and run the reset again.
"@
}
Write-Ok "Containers are running (db and $Service)."

# --- 7. Summary --------------------------------------------------------------

$port = Get-EnvValue 'WEB_PORT'
if (-not ($port -match '^\d+$')) { $port = '8000' }
$url = "http://localhost:$port/"

Write-Host ''
Write-Host '==============================================================================' -ForegroundColor White
Write-Host '  THE JOURNAL HAS BEEN RESET' -ForegroundColor Green
Write-Host '==============================================================================' -ForegroundColor White
Write-Host @"

  Address:          $url
  Setup wizard:     $url   (opens because the database is empty)

  Open the address in a browser: the journal shows the first-run setup page
  (/setup/). Create the main administrator, set the journal name and, if you
  wish, upload a logo. After that the wizard does not appear again.

  The .env file was kept: settings and secrets are unchanged. A backup, if it
  was made, is in the backups folder next to the project.

"@
