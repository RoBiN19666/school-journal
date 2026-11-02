#!/usr/bin/env bash
# =============================================================================
#  Electronic attendance journal - one-command update
# =============================================================================
#  Script for Linux and macOS servers where the journal was installed with
#  docker compose (see install.sh). It updates the code from the GitHub
#  repository and rebuilds the containers; it does not touch .env, the
#  database volume or the uploaded files volume.
#
#  What it does:
#    1. checks that it is run from the project folder (docker-compose.yml is
#       here) and that docker, docker compose and git are available;
#    2. makes a backup: docker compose exec -T web python manage.py backup
#       --output /tmp/...; the file is copied into ./backups next to the project
#       and its path is printed; if the command is not available in the current
#       version, it warns and continues - a backup can also be downloaded in the
#       journal interface;
#    3. downloads the latest code: git fetch --depth 1 origin main;
#    4. applies it: git reset --hard FETCH_HEAD;
#    5. rebuilds and starts the containers: docker compose up -d --build;
#    6. checks the journal: docker compose exec -T web python manage.py
#       healthcheck (the command returns a non-zero code on errors);
#    7. prints the version before and after the update.
#
#  Usage:
#      ./update.sh                update with a backup
#      ./update.sh --no-backup    skip the backup
#      ./update.sh --help         this help
#
#  Any local changes to tracked files are discarded by git reset --hard.
#  Untracked files (including .env) and Docker volumes are kept.
# =============================================================================
set -euo pipefail

# --- Parameters ---------------------------------------------------------------

SERVICE="web"
REMOTE="origin"
BRANCH="main"
#: The --no-backup flag: skip the backup step (1 = skip).
NO_BACKUP=0
#: The file that has to be in the current folder: otherwise the script would
#: update some other project or fail halfway through.
COMPOSE_FILE="docker-compose.yml"

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
Updating the electronic attendance journal (Django + PostgreSQL + Docker).

Usage:
    ./update.sh                update with a backup
    ./update.sh --no-backup    update without a backup
    ./update.sh --help         this help

What the script does:
    1. checks that it is run from the project folder (docker-compose.yml is
       here) and that docker, docker compose and git are available;
    2. makes a backup (python manage.py backup). The file is saved into the
       ./backups folder next to the project and its path is printed. If the
       command is not available in the current version, it warns and continues:
       download a backup in the journal interface, section "Backup";
    3. downloads the latest code from GitHub (git fetch --depth 1 origin main)
       and applies it (git reset --hard FETCH_HEAD);
    4. rebuilds and starts the containers (docker compose up -d --build);
    5. runs the self-check (python manage.py healthcheck);
    6. prints the journal version before and after the update.

Local changes to tracked files are discarded. The .env file, the database
volume and the uploaded files volume are not touched.

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

# --- Version ------------------------------------------------------------------

#: The journal version from journal/__init__.py (or "unknown").
current_version() {
    if [ ! -f journal/__init__.py ]; then
        printf 'unknown'
        return
    fi
    awk -F'"' '/^__version__[[:space:]]*=/ { print $2; exit }' journal/__init__.py
}

# --- Checks -------------------------------------------------------------------

if [ ! -f "$COMPOSE_FILE" ]; then
    die "There is no $COMPOSE_FILE in the current folder: $(pwd). Run the script from the journal folder: cd /path/to/school-journal && ./update.sh"
fi

if ! command -v docker >/dev/null 2>&1; then
    die "Docker is not installed or not in PATH. Install Docker Engine (or Docker Desktop) and run the update again: see docs/admin-guide.md, section 2."
fi

if ! docker compose version >/dev/null 2>&1; then
    die "The 'docker compose' command (Compose v2) is not available. Update Docker or install the compose plugin, then run the update again: docker compose version"
fi

if ! command -v git >/dev/null 2>&1; then
    die "git is not installed or not in PATH. Install git (for example: sudo apt install git) and run the update again."
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    die "The current folder is not a git checkout of the journal. Download the project from GitHub again or update the files by hand: see docs/admin-guide.md, section 4."
fi

VERSION_BEFORE=$(current_version)
say "Updating the electronic attendance journal."
note "Version before update: $VERSION_BEFORE"

# --- Backup -------------------------------------------------------------------

if [ "$NO_BACKUP" -eq 1 ]; then
    note "Backup skipped (--no-backup)."
else
    step "Making a backup"
    #: The journal writes the file into the container; the container is rebuilt
    #: later, so the copy is streamed to the host folder next to the project.
    BACKUP_DIR="backups"
    BACKUP_NAME="backup-$(date +%Y%m%d-%H%M%S).json"
    BACKUP_CONTAINER_PATH="/tmp/$BACKUP_NAME"
    BACKUP_SAVED=0
    if mkdir -p "$BACKUP_DIR" &&
        docker compose exec -T "$SERVICE" python manage.py backup \
            --output "$BACKUP_CONTAINER_PATH" --quiet; then
        # The file is inside the container; stream it out as it is.
        if docker compose exec -T "$SERVICE" cat "$BACKUP_CONTAINER_PATH" \
            > "$BACKUP_DIR/$BACKUP_NAME" && [ -s "$BACKUP_DIR/$BACKUP_NAME" ]; then
            BACKUP_SAVED=1
        else
            rm -f "$BACKUP_DIR/$BACKUP_NAME"
        fi
        docker compose exec -T "$SERVICE" rm -f "$BACKUP_CONTAINER_PATH" \
            >/dev/null 2>&1 || true
    fi
    if [ "$BACKUP_SAVED" -eq 1 ]; then
        ok "Backup saved: $(pwd)/$BACKUP_DIR/$BACKUP_NAME"
    else
        warn "The backup command is not available or failed - continuing without it."
        note "Download a backup in the journal interface, section \"Backup\", before updating."
        note "The database volume is kept as it is; the update itself does not delete data."
    fi
fi

# --- Update the code ----------------------------------------------------------

step "Downloading the latest version from GitHub"
if ! git fetch --depth 1 "$REMOTE" "$BRANCH"; then
    die "Could not download the update. Check the internet connection and that the remote '$REMOTE' points to GitHub (git remote -v). Nothing has been changed."
fi
ok "Latest version downloaded."

step "Applying the new version"
if ! git reset --hard FETCH_HEAD; then
    die "Could not apply the new version. The code may have local changes that conflict with the update. Check 'git status', then run the update again. The database and .env were not touched."
fi
ok "New version applied."

# --- Rebuild and start --------------------------------------------------------

step "Rebuilding and starting the containers"
note "This can take a few minutes: the image is rebuilt."
if ! docker compose up -d --build; then
    die "The journal did not start. Look at the logs: docker compose logs --tail 200 $SERVICE. The database volume and .env are kept; to return to the previous code version use 'git reset --hard <commit>' and run 'docker compose up -d --build' again."
fi
ok "Containers are up."

# --- Self-check ---------------------------------------------------------------

step "Checking the journal"
if ! docker compose exec -T "$SERVICE" python manage.py healthcheck; then
    die "The self-check reported errors (lines marked [ERROR]). Look at the logs: docker compose logs --tail 200 $SERVICE. The data is kept; fix the cause and run the self-check again: docker compose exec -T $SERVICE python manage.py healthcheck"
fi
ok "Self-check passed."

# --- Result -------------------------------------------------------------------

VERSION_AFTER=$(current_version)
step "Update finished"
say "Version before update: $VERSION_BEFORE"
say "Version after update:  $VERSION_AFTER"
note "Open the journal in the browser and press Ctrl+F5 so that it takes the new styles and scripts."
