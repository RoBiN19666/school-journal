#!/usr/bin/env bash
# =============================================================================
#  Electronic attendance journal - reset to the first-run state
# =============================================================================
#  Script for Linux and macOS (for Windows use reset.ps1).
#
#  What it does:
#    1. checks that it is run from the project folder (docker-compose.yml is
#       here);
#    2. shows what will be deleted and asks for confirmation: the word DELETE
#       typed in full (not y/yes);
#    3. unless --no-backup is given, makes a backup with
#       "python manage.py backup" and saves it into ./backups next to the
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
#      bash reset.sh                reset with a backup
#      bash reset.sh --no-backup    reset without a backup
#      bash reset.sh --help         this help
# =============================================================================
set -euo pipefail

# --- Paths and parameters -----------------------------------------------------

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

#: The service that runs the journal; its management command makes the backup.
SERVICE="web"
#: The file that has to be in the project folder: otherwise the script would
#: delete volumes of some other project or fail halfway through.
COMPOSE_FILE="docker-compose.yml"
#: The file with secrets and settings. It is never modified or deleted.
ENV_FILE=".env"
#: The --no-backup flag: skip the backup step (1 = skip).
NO_BACKUP=0

# --- Output formatting --------------------------------------------------------

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$(printf '\033[1m')
    DIM=$(printf '\033[2m')
    RED=$(printf '\033[31m')
    GREEN=$(printf '\033[32m')
    YELLOW=$(printf '\033[33m')
    RESET=$(printf '\033[0m')
else
    BOLD='' DIM='' RED='' GREEN='' YELLOW='' RESET=''
fi

say() { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$BOLD" "$*" "$RESET"; }
ok() { printf '%s  [OK]%s %s\n' "$GREEN" "$RESET" "$*"; }
note() { printf '%s       %s%s\n' "$DIM" "$*" "$RESET"; }
warn() { printf '%s  [!]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die() {
    printf '\n%s  [ERROR]%s %s\n\n' "$RED" "$RESET" "$*" >&2
    exit 1
}

# Any unexpected error - with the line number, so there is something to attach
# to a report.
trap 'printf "\n%s  [ERROR]%s A command failed (line %s).%s\n\n" "$RED" "$RESET" "$LINENO" "$RESET" >&2' ERR

usage() {
    cat <<'TEXT'
Resetting the electronic attendance journal (Django + PostgreSQL + Docker).

Usage:
    bash reset.sh                reset with a backup
    bash reset.sh --no-backup    reset without a backup
    bash reset.sh --help         this help

What the script does:
    1. checks that it is run from the project folder (docker-compose.yml is
       here) and that docker and docker compose are available;
    2. shows what will be deleted and asks for confirmation: type DELETE in
       full (not y/yes);
    3. makes a backup with python manage.py backup into ./backups next to the
       project and prints its path (unless --no-backup is given; if the web
       container is not running, the backup is skipped with a warning);
    4. removes the containers and the volumes: docker compose down -v. This
       deletes the journal database, the uploaded logo and favicon and the
       demo database;
    5. builds and starts the containers again: docker compose up -d --build.
       The journal opens the first-run setup wizard at http://localhost:8000/.

What is NOT deleted: the .env file with secrets and settings, the project
files and the backups folder.

Environment variables:
    NO_COLOR=1                 no colour in the output
TEXT
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h | --help)
            usage
            exit 0
            ;;
        --no-backup) NO_BACKUP=1 ;;
        *)
            usage >&2
            die "Unknown argument: $1"
            ;;
    esac
    shift
done

# --- Start --------------------------------------------------------------------

printf '\n%sElectronic attendance journal - reset%s\n' "$BOLD" "$RESET"
printf '%sDjango 5 + PostgreSQL + Docker, directory: %s%s\n' "$DIM" "$SCRIPT_DIR" "$RESET"

# --- 1. The project folder ----------------------------------------------------

if [ ! -f "$COMPOSE_FILE" ]; then
    die "There is no $COMPOSE_FILE in the current folder: $(pwd)
  Run the script from the journal folder:
    cd /path/to/journal && bash reset.sh"
fi

# --- 2. Warning and confirmation ---------------------------------------------

line='=============================================================================='
say ""
printf '%s%s%s\n' "$BOLD" "$line" "$RESET"
printf '%s  WARNING: THIS DELETES THE JOURNAL DATA%s\n' "$BOLD" "$RESET"
printf '%s%s%s\n' "$BOLD" "$line" "$RESET"
cat <<'TEXT'

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
TEXT
say ""

printf '%sType DELETE (in full) to continue, anything else cancels:%s ' "$BOLD" "$RESET"
answer=''
IFS= read -r answer || answer=''
if [ "$answer" != "DELETE" ]; then
    say ""
    die "Reset cancelled - nothing was changed."
fi

# --- 3. Docker and docker compose ---------------------------------------------

step "Checking Docker"

if ! command -v docker >/dev/null 2>&1; then
    die "Docker is not installed or not in PATH.
  Install Docker Engine (or Docker Desktop) and run the reset again:
  see docs/admin-guide.md, section 2."
fi

if ! docker info >/dev/null 2>&1; then
    die "Docker is installed, but its service is not responding.
  Start Docker Desktop (or: sudo systemctl start docker) and run the reset again.
  Check it yourself: docker info"
fi
ok "Docker: $(docker --version 2>/dev/null || echo 'available')"

if ! docker compose version >/dev/null 2>&1; then
    die "The 'docker compose' command (Compose v2) is not available.
  Update Docker or install the compose plugin, then run the reset again:
    docker compose version"
fi
ok "docker compose is available."

# --- 4. Backup ----------------------------------------------------------------

if [ "$NO_BACKUP" -eq 1 ]; then
    note "Backup skipped (--no-backup)."
else
    step "Making a backup"
    #: The journal writes the file into the container; it is streamed to the
    #: host folder next to the project before the volume is deleted.
    BACKUP_DIR="backups"
    BACKUP_NAME="backup-$(date +%Y%m%d-%H%M%S).json"
    BACKUP_CONTAINER_PATH="/tmp/$BACKUP_NAME"
    BACKUP_HOST_PATH="$BACKUP_DIR/$BACKUP_NAME"

    RUNNING=0
    if docker compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
        RUNNING=1
    fi

    if [ "$RUNNING" -eq 0 ]; then
        warn "The $SERVICE container is not running - continuing without a backup."
        note "The database volume is deleted later. If the data matters, start the journal and run the reset again."
    else
        if ! mkdir -p "$BACKUP_DIR"; then
            die "Could not create the $BACKUP_DIR folder. Nothing was deleted."
        fi
        if ! docker compose exec -T "$SERVICE" python manage.py backup \
            --output "$BACKUP_CONTAINER_PATH" --quiet; then
            die "Could not make a backup. Nothing was deleted.
  Check the logs: docker compose logs --tail=100 $SERVICE
  To reset without a backup, run: bash reset.sh --no-backup"
        fi
        if docker compose exec -T "$SERVICE" cat "$BACKUP_CONTAINER_PATH" \
            > "$BACKUP_HOST_PATH" && [ -s "$BACKUP_HOST_PATH" ]; then
            ok "Backup saved: $(pwd)/$BACKUP_HOST_PATH"
        else
            rm -f "$BACKUP_HOST_PATH"
            docker compose exec -T "$SERVICE" rm -f "$BACKUP_CONTAINER_PATH" \
                >/dev/null 2>&1 || true
            die "Could not copy the backup to $BACKUP_HOST_PATH. Nothing was deleted.
  To reset without a backup, run: bash reset.sh --no-backup"
        fi
        docker compose exec -T "$SERVICE" rm -f "$BACKUP_CONTAINER_PATH" \
            >/dev/null 2>&1 || true
    fi
fi

# --- 5. Remove the containers and the data volumes ----------------------------

step "Removing the containers and the data volumes"
note "This deletes the journal database, the uploaded logo and favicon and the demo database."
if ! docker compose down -v; then
    die "Could not remove the containers and the volumes. Nothing else was changed.
  Check the reason: docker compose ps"
fi
ok "Containers and volumes removed."

# --- 6. Build and start -------------------------------------------------------

step "Building and starting the containers"
note "The first build can take a few minutes: images are downloaded and libraries are installed."
if ! docker compose up -d --build; then
    die "The containers did not start.
  Look at the reason: docker compose logs --tail=100 $SERVICE
  The data volumes were already deleted; fix the cause and run the reset again."
fi
ok "Containers are running (db and $SERVICE)."

# --- 7. Summary ---------------------------------------------------------------

#: The port can be changed in .env; read it without modifying the file.
port=''
if [ -f "$ENV_FILE" ]; then
    port=$(awk -F= '
        {
            line = $0
            sub(/^\357\273\277/, "", line)
        }
        index(line, "WEB_PORT=") == 1 {
            value = substr(line, index(line, "=") + 1)
            sub(/\r$/, "", value)
            print value
            exit
        }
    ' "$ENV_FILE")
fi
case "$port" in
    '' | *[!0-9]*) port=8000 ;;
esac
url="http://localhost:$port/"

printf '\n%s%s%s\n' "$BOLD" "$line" "$RESET"
printf '%s  THE JOURNAL HAS BEEN RESET%s\n' "$BOLD" "$RESET"
printf '%s%s%s\n' "$BOLD" "$line" "$RESET"

cat <<TEXT

  Address:          $url
  Setup wizard:     $url   (opens because the database is empty)

  Open the address in a browser: the journal shows the first-run setup page
  (/setup/). Create the main administrator, set the journal name and, if you
  wish, upload a logo. After that the wizard does not appear again.

  The .env file was kept: settings and secrets are unchanged. A backup, if it
  was made, is in the backups folder next to the project.

TEXT
