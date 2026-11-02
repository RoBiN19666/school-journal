#!/usr/bin/env bash
# =============================================================================
#  Electronic attendance journal - one-command installation
# =============================================================================
#  Script for Linux and macOS (for Windows use install.ps1).
#
#  What it does:
#    1. checks that Docker and docker compose are available; if Docker is
#       missing, on Linux it offers to install it (Ubuntu 22.04/24.04 and
#       derivatives, Debian 11/12, Fedora/RHEL/AlmaLinux/Rocky 9+), while on
#       macOS and Windows it installs nothing and gives manual instructions:
#       Docker Desktop is a separate application with a graphical wizard and a
#       paid licence for large organisations;
#    2. creates .env from .env.example (if .env does not exist yet) with random
#       SECRET_KEY and POSTGRES_PASSWORD and working default settings;
#    3. builds and starts the containers: docker compose up -d --build;
#    4. waits until the journal answers its self-check (manage.py healthcheck);
#    5. prints the journal address and what to do on first opening.
#
#  Usage:
#      bash install.sh
#      bash install.sh --install-docker   agree to install Docker (Linux)
#      bash install.sh --no-prompt        no questions: instructions only
#
#  Re-running is safe: an existing .env is not overwritten, secrets are not
#  regenerated, data in the volumes is left alone - only the build and the
#  start are performed. The same script is used to update to a new version.
# =============================================================================
set -euo pipefail

# --- Paths and parameters -----------------------------------------------------

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

ENV_FILE=".env"
ENV_TEMPLATE=".env.example"
SERVICE="web"
#: How many seconds to wait for the journal to become ready (can be overridden
#: in the environment).
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-300}
#: Pause between readiness checks, seconds.
HEALTH_INTERVAL=${HEALTH_INTERVAL:-4}
#: Agree to install Docker without asking (the --install-docker flag).
INSTALL_DOCKER=0
#: No questions at all, instructions only (the --no-prompt flag).
NO_PROMPT=0
#: File describing the distribution (Linux). Can be overridden - needed for tests.
OS_RELEASE_FILE=${OS_RELEASE_FILE:-/etc/os-release}

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
Installing the electronic attendance journal (Django + PostgreSQL + Docker).

Usage:
    bash install.sh                    install or update the journal
    bash install.sh --install-docker   agree to install Docker without asking
                                       (Linux only, supported distributions
                                       only; also works without a terminal -
                                       for automation)
    bash install.sh --no-prompt        no questions: instructions only
    bash install.sh --help             this help

The journal cannot run without Docker. On Linux the script checks the
distribution in /etc/os-release and, if the system is supported, asks
"Install Docker now? [y/N]" - the default answer is NO (just press Enter).
In a non-interactive environment (no terminal) the question is not asked:
the script prints the commands for a manual installation and exits with
code 1.

On macOS and Windows there is no automatic installation: Docker Desktop is
installed by hand. It is a large application with a graphical wizard, it has
to be started after installation, and the Docker Desktop licence for large
organisations is paid - the decision to install must stay with a human. The
script shows the official download page and a warning about the licence.

Environment variables:
    HEALTH_TIMEOUT=600                 how many seconds to wait for readiness (default 300)
    NO_COLOR=1                         no colour in the output

The script creates .env (if it does not exist), builds the containers and waits
until the journal answers its self-check. Re-running is safe: settings and data
are not overwritten.
TEXT
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h | --help)
            usage
            exit 0
            ;;
        --install-docker) INSTALL_DOCKER=1 ;;
        --no-prompt) NO_PROMPT=1 ;;
        *)
            usage >&2
            die "Unknown argument: $1"
            ;;
    esac
    shift
done

# --- Helper functions ---------------------------------------------------------

#: Reads a variable value from .env (without quotes and without comments).
env_value() {
    awk -v key="$1" '
        index($0, key "=") == 1 {
            print substr($0, length(key) + 2)
            exit
        }
    ' "$ENV_FILE"
}

#: Writes KEY=VALUE to .env: replaces the existing line or adds a new one.
set_env() {
    key=$1
    value=$2
    tmp="$ENV_FILE.tmp.$$"
    awk -v k="$key" -v v="$value" '
        index($0, k "=") == 1 { print k "=" v; replaced = 1; next }
        { print }
        END { if (!replaced) print k "=" v }
    ' "$ENV_FILE" >"$tmp"
    mv "$tmp" "$ENV_FILE"
}

#: Random string of Latin letters and digits, length $1: for keys and passwords.
#: Tries openssl, python3 and /dev/urandom in turn - the first one that returns
#: a string of the required length wins (python3 may be a Microsoft Store stub).
random_string() {
    length=$1
    value=''
    if command -v openssl >/dev/null 2>&1; then
        value=$(openssl rand -base64 256 2>/dev/null | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-"$length" || true)
    fi
    if [ "${#value}" -lt "$length" ] && command -v python3 >/dev/null 2>&1; then
        value=$(python3 -c 'import secrets, string, sys
alphabet = string.ascii_letters + string.digits
print("".join(secrets.choice(alphabet) for _ in range(int(sys.argv[1]))))' "$length" 2>/dev/null || true)
    fi
    if [ "${#value}" -lt "$length" ]; then
        value=$(dd if=/dev/urandom bs=1024 count=1 2>/dev/null | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-"$length" || true)
    fi
    printf '%s' "$value"
}

#: Wrapper around "docker compose" / the older "docker-compose".
compose() {
    if [ "$COMPOSE_KIND" = "v1" ]; then
        docker-compose "$@"
    else
        docker compose "$@"
    fi
}

# --- When Docker is not installed ---------------------------------------------

#: Value of a key from the distribution description file (/etc/os-release).
#: Quotes around the value are stripped, as the file itself does.
#: The path can be overridden with OS_RELEASE_FILE (needed for tests).
os_release_value() {
    key=$1
    [ -r "$OS_RELEASE_FILE" ] || return 0
    awk -F= -v k="$key" '
        $1 == k {
            value = substr($0, index($0, "=") + 1)
            gsub(/^"|"$/, "", value)
            print value
            exit
        }
    ' "$OS_RELEASE_FILE"
}

#: Platform: linux, macos, windows (bash on Windows) or unknown.
detect_os() {
    kernel=$(uname -s 2>/dev/null || printf 'unknown')
    case "$kernel" in
        Linux*) printf 'linux' ;;
        Darwin*) printf 'macos' ;;
        MINGW* | MSYS* | CYGWIN* | Windows* | *_NT*) printf 'windows' ;;
        *) printf 'unknown' ;;
    esac
}

#: Reads the distribution description into the DISTRO_* variables.
read_linux_distro() {
    DISTRO_ID=$(os_release_value ID | tr 'A-Z' 'a-z')
    DISTRO_LIKE=$(os_release_value ID_LIKE | tr 'A-Z' 'a-z')
    DISTRO_VERSION=$(os_release_value VERSION_ID)
    DISTRO_CODENAME=$(os_release_value VERSION_CODENAME)
    DISTRO_UBUNTU_CODENAME=$(os_release_value UBUNTU_CODENAME | tr 'A-Z' 'a-z')
    DISTRO_PRETTY=$(os_release_value PRETTY_NAME)
    DISTRO_MAJOR=${DISTRO_VERSION%%.*}
}

#: Distribution family: ubuntu, debian, fedora or other.
linux_family() {
    case " $DISTRO_ID $DISTRO_LIKE " in
        *" ubuntu "*) printf 'ubuntu' ;;
        *" debian "*) printf 'debian' ;;
        *" fedora "* | *" rhel "* | *" centos "*) printf 'fedora' ;;
        *) printf 'other' ;;
    esac
}

#: Whether installing Docker automatically makes sense on this system.
#: Ubuntu 22.04/24.04 and derivatives, Debian 11/12, Fedora/RHEL/Alma/Rocky 9+.
linux_supported() {
    family=$1
    case "$family" in
        ubuntu)
            case "$DISTRO_VERSION" in
                22.04 | 22.04.* | 24.04 | 24.04.*) return 0 ;;
            esac
            case "$DISTRO_UBUNTU_CODENAME" in
                jammy | noble) return 0 ;;
            esac
            return 1
            ;;
        debian)
            case "$DISTRO_MAJOR" in
                11 | 12) return 0 ;;
            esac
            return 1
            ;;
        fedora)
            case "$DISTRO_MAJOR" in
                '' | *[!0-9]*) return 1 ;;
            esac
            if [ "$DISTRO_MAJOR" -ge 9 ] 2>/dev/null; then
                return 0
            fi
            return 1
            ;;
        *) return 1 ;;
    esac
}

#: Line "System: Ubuntu 24.04.1 LTS (ubuntu, jammy 24.04)".
describe_linux() {
    distro_info="$DISTRO_ID"
    if [ -n "$DISTRO_CODENAME" ]; then
        distro_info="$distro_info, $DISTRO_CODENAME"
    fi
    if [ -n "$DISTRO_VERSION" ]; then
        distro_info="$distro_info $DISTRO_VERSION"
    fi
    say "System: ${DISTRO_PRETTY:-$DISTRO_ID} ($distro_info)"
}

#: Hint for a supported system when the user declined.
print_linux_install_hint() {
    say ""
    say "${BOLD}Install Docker yourself:${RESET}"
    say "  curl -fsSL https://get.docker.com | sudo sh"
    say "  sudo usermod -aG docker \$USER"
    say "  newgrp docker        # or log out and log in again"
    say "  bash install.sh"
    say ""
    say "  Details per distribution: https://docs.docker.com/engine/install/"
}

#: The system is not in the supported matrix: we install nothing, we give commands.
print_unsupported_linux() {
    say ""
    say "Your system ($DISTRO_ID ${DISTRO_VERSION:-no version}) is not on the list"
    say "of systems supported for automatic installation - I will not install Docker"
    say "myself, so as not to break the system."
    say ""
    say "${BOLD}Install Docker manually:${RESET}"
    case "$DISTRO_ID" in
        arch* | manjaro* | endeavouros*)
            say "  sudo pacman -S docker docker-compose"
            ;;
        opensuse* | suse* | sled*)
            say "  sudo zypper install docker docker-compose"
            ;;
        alpine*)
            say "  sudo apk add docker docker-cli-compose"
            ;;
        *)
            say "  Install docker-ce and the compose plugin with the system package manager."
            ;;
    esac
    say "  Instructions: https://docs.docker.com/engine/install/"
    say "  Then run: bash install.sh"
}

#: macOS: we do not offer an installation and start nothing.
print_macos_message() {
    cat <<'TEXT'

Docker Desktop for macOS is installed by hand: it is a large application
with a graphical wizard, it has to be started after installation, and
sometimes the system has to be rebooted as well. Besides, the Docker Desktop
licence for large organisations is paid - the decision to install must be
yours. I do not install it and I do not download anything.
TEXT
    say ""
    say "  Download page: https://www.docker.com/products/docker-desktop/"
    say "  Installing on macOS: https://docs.docker.com/desktop/install/mac-install/"
    say ""
    say "After installation start Docker Desktop and run again: bash install.sh"
}

#: Windows: we do not offer an installation and start nothing.
print_windows_message() {
    cat <<'TEXT'

This looks like bash on Windows. Docker Desktop for Windows is installed by
hand: it is a large application with a graphical wizard, a reboot is required
after installation, and the Docker Desktop licence for large organisations is
paid. I do not install it and I do not download anything.
TEXT
    say ""
    say "  Download page: https://www.docker.com/products/docker-desktop/"
    say "  Installing on Windows: https://docs.docker.com/desktop/install/windows-install/"
    say ""
    say "The journal installer for PowerShell does not install Docker by itself either:"
    say "  powershell -ExecutionPolicy Bypass -File .\\install.ps1"
    say ""
    say "After installing Docker Desktop, run the installation again."
}

print_generic_message() {
    say ""
    say "Could not detect the system. Install Docker manually:"
    say "  https://docs.docker.com/engine/install/"
    say "  https://www.docker.com/products/docker-desktop/"
    say ""
    say "After installation run again: bash install.sh"
}

#: Linux: the official get.docker.com installer, then the docker group.
install_docker_linux() {
    say ""

    if ! command -v curl >/dev/null 2>&1; then
        die "curl is required for the installation.
  Debian/Ubuntu:  sudo apt-get install -y curl
  Fedora/RHEL:    sudo dnf install -y curl
  Then run again: bash install.sh"
    fi

    if command -v id >/dev/null 2>&1 && [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; then
        SUDO=''
    elif command -v sudo >/dev/null 2>&1; then
        SUDO=sudo
    else
        die "Root rights or an installed sudo are required.
  Run the script as root:  su -c 'bash install.sh'"
    fi

    script_path="${TMPDIR:-/tmp}/get-docker.$$.sh"
    say "Running: curl -fsSL https://get.docker.com -o $script_path"
    if ! curl -fsSL https://get.docker.com -o "$script_path"; then
        rm -f "$script_path"
        die "Could not download the Docker installer from https://get.docker.com.
  Check the internet connection and try again, or install Docker manually:
    https://docs.docker.com/engine/install/"
    fi
    if [ ! -s "$script_path" ]; then
        rm -f "$script_path"
        die "The Docker installer was downloaded empty. Install Docker manually: https://docs.docker.com/engine/install/"
    fi

    say "Running: $SUDO sh $script_path"
    if ! $SUDO sh "$script_path"; then
        rm -f "$script_path"
        die "The Docker installer failed (messages above).
  Install Docker manually: https://docs.docker.com/engine/install/"
    fi
    rm -f "$script_path"

    current_user=${USER:-$(id -un 2>/dev/null || echo user)}
    say "Running: $SUDO usermod -aG docker $current_user"
    if ! $SUDO usermod -aG docker "$current_user"; then
        warn "Could not add $current_user to the docker group - run docker through sudo."
    fi

    cat <<TEXT

Docker is installed. Two steps are left before the journal can start:

  1. Log out and log in again (or run  newgrp docker ) - otherwise docker will
     not let you in without sudo: the new group only applies to a new session.
  2. Run the installation again:  bash install.sh

Check that Docker works:  docker run --rm hello-world
TEXT
    exit 0
}

#: Single handling of "Docker not found" / "docker compose missing".
#: $1 - what is missing: "docker" or "docker compose".
handle_missing_docker() {
    missing=$1
    os=$(detect_os)

    say ""
    if [ "$missing" = "docker" ]; then
        warn "Docker not found."
        say "It is required to run the journal in containers: PostgreSQL and the application itself."
    else
        warn 'Docker is installed, but there is no "docker compose".'
        say "The compose plugin is required to bring up the database and the application with one command."
    fi

    case "$os" in
        linux)
            handle_missing_docker_linux
            ;;
        macos)
            print_macos_message
            exit 1
            ;;
        windows)
            print_windows_message
            exit 1
            ;;
        *)
            print_generic_message
            exit 1
            ;;
    esac
}

#: Linux: decide from /etc/os-release whether Docker can be installed automatically.
handle_missing_docker_linux() {
    read_linux_distro
    family=$(linux_family)
    say ""
    describe_linux

    if ! linux_supported "$family"; then
        print_unsupported_linux
        exit 1
    fi

    say "Installing Docker with the official get.docker.com script."

    if [ "$NO_PROMPT" -eq 1 ]; then
        say ""
        say "Flag --no-prompt: not asking questions, installing nothing."
        print_linux_install_hint
        exit 1
    fi

    if [ "$INSTALL_DOCKER" -eq 1 ]; then
        say "Flag --install-docker: installing Docker without asking."
    else
        if [ ! -t 0 ]; then
            say ""
            say "No input available (no terminal): not asking questions, installing nothing."
            say "To install automatically, run with the --install-docker flag."
            print_linux_install_hint
            exit 1
        fi

        printf '\n%sInstall Docker now?%s [y/N] ' "$BOLD" "$RESET"
        answer=''
        IFS= read -r answer || answer=''
        case "$answer" in
            y | Y | yes | YES | Yes) ;;
            *)
                say ""
                say "Installation cancelled - nothing on this machine was changed."
                print_linux_install_hint
                exit 1
                ;;
        esac
    fi

    install_docker_linux
}

# --- Start --------------------------------------------------------------------

printf '\n%sElectronic attendance journal - installation%s\n' "$BOLD" "$RESET"
printf '%sDjango 5 + PostgreSQL + Docker, directory: %s%s\n' "$DIM" "$SCRIPT_DIR" "$RESET"

# --- 1. Docker and docker compose ---------------------------------------------

step "Checking Docker"

if ! command -v docker >/dev/null 2>&1; then
    handle_missing_docker "docker"
fi

if ! docker info >/dev/null 2>&1; then
    die "Docker is installed, but its service is not responding.
  Start Docker Desktop (or: sudo systemctl start docker) and run the installation again.
  Check it yourself: docker info"
fi
ok "Docker: $(docker --version 2>/dev/null || echo 'available')"

COMPOSE_KIND="v2"
if docker compose version >/dev/null 2>&1; then
    COMPOSE_KIND="v2"
    ok "docker compose: $(docker compose version --short 2>/dev/null || echo 'available')"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_KIND="v1"
    warn "An outdated docker-compose (v1) was found. It works, but it is better to install docker compose v2."
    ok "docker-compose: $(docker-compose --version 2>/dev/null || echo 'available')"
else
    handle_missing_docker "docker compose"
fi

# --- 2. The .env file ---------------------------------------------------------

step "Preparing settings (.env)"

if [ -f "$ENV_FILE" ]; then
    ok "The .env file already exists - leaving it as it is."
    note "Secrets and settings are not recreated: re-running does not change them."
else
    if [ ! -f "$ENV_TEMPLATE" ]; then
        die "The file $ENV_TEMPLATE is missing - download the project in full (the settings template is part of it)."
    fi
    cp "$ENV_TEMPLATE" "$ENV_FILE"
    chmod 600 "$ENV_FILE" 2>/dev/null || true

    secret_key=$(random_string 64)
    db_password=$(random_string 40)

    set_env SECRET_KEY "$secret_key"
    set_env POSTGRES_PASSWORD "$db_password"
    set_env DEBUG "0"
    set_env ALLOWED_HOSTS "localhost,127.0.0.1"
    set_env DJANGO_LANGUAGE_CODE "ru"

    # Just in case: without a key of the required length the journal will not start.
    if [ "${#secret_key}" -lt 50 ] || [ "${#db_password}" -lt 16 ]; then
        rm -f "$ENV_FILE"
        die "Could not generate random values (no openssl, python3 or /dev/urandom).
  Create .env manually from .env.example and fill in SECRET_KEY and POSTGRES_PASSWORD."
    fi
    ok "The .env file has been created from $ENV_TEMPLATE."
    note "SECRET_KEY and POSTGRES_PASSWORD were generated randomly and are stored only in .env."
fi

# Required values: without them the containers will not come up, better to say it
# right away.
missing=""
secret_key=$(env_value SECRET_KEY)
db_password=$(env_value POSTGRES_PASSWORD)
debug=$(env_value DEBUG)
allowed_hosts=$(env_value ALLOWED_HOSTS)

[ -n "$secret_key" ] || missing="$missing SECRET_KEY"
[ -n "$db_password" ] || missing="$missing POSTGRES_PASSWORD"
[ -n "$debug" ] || missing="$missing DEBUG"
[ -n "$allowed_hosts" ] || missing="$missing ALLOWED_HOSTS"
if [ -n "$missing" ]; then
    die "The .env file does not set:$missing
  Open .env and fill them in (hints are in .env.example) or delete .env
  and run install.sh again: it will create the file with random secrets."
fi
if [ "${#secret_key}" -lt 50 ]; then
    die "SECRET_KEY in .env is shorter than 50 characters - the journal will not start like that.
  Generate a new key: openssl rand -base64 64 | tr -dc 'A-Za-z0-9' | cut -c1-64
  Note: changing SECRET_KEY signs all users out of the journal."
fi
ok "Required settings are in place (DEBUG=$debug, ALLOWED_HOSTS=$allowed_hosts)."

# --- 3. Compose configuration -------------------------------------------------

step "Checking the configuration"

if ! compose config -q; then
    die "docker compose rejected the configuration (message above).
  This is usually an unset or wrong variable in .env - compare it with .env.example."
fi
ok "The configuration is valid."

# --- 4. Build and start -------------------------------------------------------

step "Building and starting containers"
note "The first build takes a few minutes: images are downloaded and libraries are installed."
if ! compose up -d --build; then
    die "The containers did not start.
  Look at the reason: docker compose logs --tail=100 $SERVICE
  Common cases (no Docker Hub mirror, port 8000 already in use) are covered in
  the README, section 'If something went wrong'."
fi
ok "Containers are running (db and $SERVICE)."

# --- 5. Waiting for readiness -------------------------------------------------

step "Waiting for the journal to become ready"
note "Self-check every ${HEALTH_INTERVAL} s, ${HEALTH_TIMEOUT} s maximum."

started=$(date +%s)
not_running=0
ready=0
while [ "$ready" -eq 0 ]; do
    if output=$(compose exec -T "$SERVICE" python manage.py healthcheck 2>&1); then
        ready=1
        break
    fi

    case "$output" in
        *"is not running"* | *"not running"*)
            not_running=$((not_running + 1))
            ;;
        *)
            not_running=0
            ;;
    esac
    if [ "$not_running" -ge 3 ]; then
        printf '\n'
        warn "Container $SERVICE is not running - the journal will not come up."
        compose logs --tail=40 "$SERVICE" || true
        die "The journal did not start.
  See the full logs: docker compose logs $SERVICE
  The cause is usually visible in the first lines of the container log - the
  migration step and the start of the application."
    fi

    now=$(date +%s)
    if [ $((now - started)) -ge "$HEALTH_TIMEOUT" ]; then
        printf '\n'
        warn "The journal did not answer within ${HEALTH_TIMEOUT} s."
        compose ps || true
        compose logs --tail=60 "$SERVICE" || true
        die "The journal did not come up within ${HEALTH_TIMEOUT} s.
  Logs: docker compose logs $SERVICE
  If the build is still downloading images - run it again, the progress is kept.
  Another timeout: HEALTH_TIMEOUT=900 bash install.sh"
    fi

    printf '.'
    sleep "$HEALTH_INTERVAL"
done
printf '\n'
ok "The journal is answering, the self-check is clean."

# --- 6. Summary ---------------------------------------------------------------

port=$(env_value WEB_PORT)
case "$port" in
    '' | *[!0-9]*) port=8000 ;;
esac
url="http://localhost:$port"

line='=============================================================================='
printf '\n%s%s%s\n' "$BOLD" "$line" "$RESET"
printf '%s  ELECTRONIC ATTENDANCE JOURNAL IS RUNNING%s\n' "$BOLD" "$RESET"
printf '%s%s%s\n' "$BOLD" "$line" "$RESET"

cat <<TEXT

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
  2. Restart: bash install.sh   (or: docker compose up -d)
  3. Open $url/demo/ and click "Sign in as the methodist"
     or "Sign in as the teacher".
  Demo data lives in a separate database and does not affect the working journal.
  Before real work set DEMO_MODE=0 back.

  DATA AND BACKUPS
  The database and the uploaded files live in the postgres_data and media_data
  volumes and are kept through updates and shutdowns. A copy of all data is the
  "Backup" section in the journal (administrator only).

  USEFUL COMMANDS (in the project directory)
    docker compose logs -f $SERVICE          follow the logs
    docker compose ps                   container status
    docker compose down                 stop (data is kept)
    docker compose up -d --build        start after replacing files

  Settings are in the .env file, details are in README.md
  and docs/admin-guide.md.

TEXT
