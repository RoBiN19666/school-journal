#!/usr/bin/env bash
# =============================================================================
#  Электронный журнал посещаемости — установка одной командой
# =============================================================================
#  Скрипт для Linux и macOS (для Windows — install.ps1).
#
#  Что делает:
#    1. проверяет, что есть Docker и docker compose; если Docker нет —
#       на Linux предлагает установить его (Ubuntu 22.04/24.04 и производные,
#       Debian 11/12, Fedora/RHEL/AlmaLinux/Rocky 9+), а на macOS и Windows
#       ничего не ставит и даёт инструкцию вручную: Docker Desktop — это
#       отдельное приложение с графическим мастером и платной лицензией
#       для крупных организаций;
#    2. создаёт .env из .env.example (если .env ещё нет) со случайными
#       SECRET_KEY и POSTGRES_PASSWORD и рабочими настройками по умолчанию;
#    3. собирает и запускает контейнеры: docker compose up -d --build;
#    4. ждёт, пока журнал ответит на самопроверку (manage.py healthcheck);
#    5. печатает адрес журнала и что делать при первом открытии.
#
#  Запуск:
#      bash install.sh
#      bash install.sh --install-docker   согласие на установку Docker (Linux)
#      bash install.sh --no-prompt        без вопросов: только инструкции
#
#  Повторный запуск безопасен: существующий .env не переписывается, секреты
#  не пересоздаются, данные в томах не трогаются — выполняется только сборка
#  и запуск. Это же используется для обновления версии.
# =============================================================================
set -euo pipefail

# --- Пути и параметры ---------------------------------------------------------

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

ENV_FILE=".env"
ENV_TEMPLATE=".env.example"
SERVICE="web"
#: Сколько секунд ждать готовности журнала (можно переопределить в окружении).
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-300}
#: Пауза между проверками готовности, секунд.
HEALTH_INTERVAL=${HEALTH_INTERVAL:-4}
#: Согласие на установку Docker без вопроса (флаг --install-docker).
INSTALL_DOCKER=0
#: Никаких вопросов, только инструкции (флаг --no-prompt).
NO_PROMPT=0
#: Файл с описанием дистрибутива (Linux). Можно переопределить — нужно для тестов.
OS_RELEASE_FILE=${OS_RELEASE_FILE:-/etc/os-release}

# --- Оформление вывода --------------------------------------------------------

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
    printf '\n%s  [ОШИБКА]%s %s\n\n' "$RED" "$*" "$RESET" >&2
    exit 1
}

# Любая неожиданная ошибка — с номером строки, чтобы отчёт было о чём приложить.
trap 'printf "\n%s  [ОШИБКА]%s Команда завершилась с ошибкой (строка %s).%s\n\n" "$RED" "$LINENO" "$RESET" >&2' ERR

usage() {
    cat <<'TEXT'
Установка электронного журнала посещаемости (Django + PostgreSQL + Docker).

Использование:
    bash install.sh                    установить или обновить журнал
    bash install.sh --install-docker   согласие на установку Docker без вопроса
                                       (только Linux, только поддерживаемые
                                       дистрибутивы; работает и без терминала —
                                       для автоматизации)
    bash install.sh --no-prompt        без вопросов: только инструкции
    bash install.sh --help             эта справка

Без Docker журнал не запустить. На Linux скрипт проверяет дистрибутив
по /etc/os-release и, если система поддерживается, спрашивает
«Установить сейчас? [y/N]» — по умолчанию ответ НЕТ (просто Enter).
В неинтерактивном окружении (нет терминала) вопрос не задаётся: скрипт
печатает команды для установки вручную и выходит с кодом 1.

На macOS и Windows автоматической установки нет: Docker Desktop ставится
вручную. Это большое приложение с графическим мастером, после установки
требуется его запустить, а лицензия Docker Desktop для крупных организаций
платная — решение об установке должно остаться за человеком. Скрипт покажет
официальную страницу загрузки и предупреждение про лицензию.

Переменные окружения:
    HEALTH_TIMEOUT=600                 сколько секунд ждать готовности (по умолчанию 300)
    NO_COLOR=1                         без цвета в выводе

Скрипт создаёт .env (если его нет), собирает контейнеры и ждёт, пока журнал
ответит на самопроверку. Повторный запуск безопасен: настройки и данные
не перезаписываются.
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
            die "Неизвестный аргумент: $1"
            ;;
    esac
    shift
done

# --- Вспомогательные функции --------------------------------------------------

#: Читает значение переменной из .env (без кавычек и без комментариев).
env_value() {
    awk -v key="$1" '
        index($0, key "=") == 1 {
            print substr($0, length(key) + 2)
            exit
        }
    ' "$ENV_FILE"
}

#: Записывает KEY=VALUE в .env: заменяет существующую строку или добавляет новую.
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

#: Случайная строка из латиницы и цифр длиной $1: для ключей и паролей.
#: Пробует по очереди openssl, python3 и /dev/urandom — первый, кто дал строку
#: нужной длины, и выигрывает (python3 бывает заглушкой из Microsoft Store).
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

#: Обёртка над «docker compose» / старым «docker-compose».
compose() {
    if [ "$COMPOSE_KIND" = "v1" ]; then
        docker-compose "$@"
    else
        docker compose "$@"
    fi
}

# --- Если Docker не установлен ------------------------------------------------

#: Значение ключа из файла описания дистрибутива (/etc/os-release).
#: Кавычки вокруг значения снимаются, как в самом файле.
#: Путь можно переопределить переменной OS_RELEASE_FILE (нужно для тестов).
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

#: Платформа: linux, macos, windows (bash в Windows) или unknown.
detect_os() {
    kernel=$(uname -s 2>/dev/null || printf 'unknown')
    case "$kernel" in
        Linux*) printf 'linux' ;;
        Darwin*) printf 'macos' ;;
        MINGW* | MSYS* | CYGWIN* | Windows* | *_NT*) printf 'windows' ;;
        *) printf 'unknown' ;;
    esac
}

#: Читает описание дистрибутива в переменные DISTRO_*.
read_linux_distro() {
    DISTRO_ID=$(os_release_value ID | tr 'A-Z' 'a-z')
    DISTRO_LIKE=$(os_release_value ID_LIKE | tr 'A-Z' 'a-z')
    DISTRO_VERSION=$(os_release_value VERSION_ID)
    DISTRO_CODENAME=$(os_release_value VERSION_CODENAME)
    DISTRO_UBUNTU_CODENAME=$(os_release_value UBUNTU_CODENAME | tr 'A-Z' 'a-z')
    DISTRO_PRETTY=$(os_release_value PRETTY_NAME)
    DISTRO_MAJOR=${DISTRO_VERSION%%.*}
}

#: Семейство дистрибутива: ubuntu, debian, fedora или other.
linux_family() {
    case " $DISTRO_ID $DISTRO_LIKE " in
        *" ubuntu "*) printf 'ubuntu' ;;
        *" debian "*) printf 'debian' ;;
        *" fedora "* | *" rhel "* | *" centos "*) printf 'fedora' ;;
        *) printf 'other' ;;
    esac
}

#: Есть ли смысл ставить Docker автоматически на этой системе.
#: Ubuntu 22.04/24.04 и производные, Debian 11/12, Fedora/RHEL/Alma/Rocky 9+.
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

#: Строка «Система: Ubuntu 24.04.1 LTS (ubuntu, jammy 24.04)».
describe_linux() {
    distro_info="$DISTRO_ID"
    if [ -n "$DISTRO_CODENAME" ]; then
        distro_info="$distro_info, $DISTRO_CODENAME"
    fi
    if [ -n "$DISTRO_VERSION" ]; then
        distro_info="$distro_info $DISTRO_VERSION"
    fi
    say "Система: ${DISTRO_PRETTY:-$DISTRO_ID} ($distro_info)"
}

#: Подсказка для поддерживаемой системы, если пользователь отказался.
print_linux_install_hint() {
    say ""
    say "${BOLD}Установите Docker самостоятельно:${RESET}"
    say "  curl -fsSL https://get.docker.com | sudo sh"
    say "  sudo usermod -aG docker \$USER"
    say "  newgrp docker        # или выйдите и войдите снова"
    say "  bash install.sh"
    say ""
    say "  Подробно по дистрибутивам: https://docs.docker.com/engine/install/"
}

#: Система не в матрице поддерживаемых: ставить не будем, дадим команды.
print_unsupported_linux() {
    say ""
    say "Ваша система ($DISTRO_ID ${DISTRO_VERSION:-без версии}) не в списке"
    say "поддерживаемых для автоматической установки — ставить Docker сам не буду,"
    say "чтобы не сломать систему."
    say ""
    say "${BOLD}Поставьте Docker вручную:${RESET}"
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
            say "  Установите docker-ce и плагин compose пакетным менеджером системы."
            ;;
    esac
    say "  Инструкция: https://docs.docker.com/engine/install/"
    say "  Затем запустите: bash install.sh"
}

#: macOS: установку не предлагаем и ничего не запускаем.
print_macos_message() {
    cat <<'TEXT'

Docker Desktop для macOS ставится вручную: это большое приложение
с графическим мастером, после установки его нужно запустить, а иногда
и перезагрузить систему. Кроме того, лицензия Docker Desktop для крупных
организаций платная — решение об установке должно остаться за вами.
Сам я его не ставлю и ничего не скачиваю.
TEXT
    say ""
    say "  Страница загрузки: https://www.docker.com/products/docker-desktop/"
    say "  Установка на macOS: https://docs.docker.com/desktop/install/mac-install/"
    say ""
    say "После установки запустите Docker Desktop и повторите: bash install.sh"
}

#: Windows: установку не предлагаем и ничего не запускаем.
print_windows_message() {
    cat <<'TEXT'

Похоже, это bash на Windows. Docker Desktop для Windows ставится вручную:
это большое приложение с графическим мастером, после установки требуется
перезагрузка, а лицензия Docker Desktop для крупных организаций платная.
Сам я его не ставлю и ничего не скачиваю.
TEXT
    say ""
    say "  Страница загрузки: https://www.docker.com/products/docker-desktop/"
    say "  Установка на Windows: https://docs.docker.com/desktop/install/windows-install/"
    say ""
    say "Установщик журнала для PowerShell тоже не ставит Docker сам:"
    say "  powershell -ExecutionPolicy Bypass -File .\\install.ps1"
    say ""
    say "После установки Docker Desktop запустите установку снова."
}

print_generic_message() {
    say ""
    say "Не удалось определить систему. Поставьте Docker вручную:"
    say "  https://docs.docker.com/engine/install/"
    say "  https://www.docker.com/products/docker-desktop/"
    say ""
    say "После установки повторите: bash install.sh"
}

#: Linux: официальный установщик get.docker.com, затем группа docker.
install_docker_linux() {
    say ""

    if ! command -v curl >/dev/null 2>&1; then
        die "Для установки нужен curl.
  Debian/Ubuntu:  sudo apt-get install -y curl
  Fedora/RHEL:    sudo dnf install -y curl
  Потом повторите: bash install.sh"
    fi

    if command -v id >/dev/null 2>&1 && [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; then
        SUDO=''
    elif command -v sudo >/dev/null 2>&1; then
        SUDO=sudo
    else
        die "Нужны права root или установленный sudo.
  Запустите скрипт от root:  su -c 'bash install.sh'"
    fi

    script_path="${TMPDIR:-/tmp}/get-docker.$$.sh"
    say "Выполняю: curl -fsSL https://get.docker.com -o $script_path"
    if ! curl -fsSL https://get.docker.com -o "$script_path"; then
        rm -f "$script_path"
        die "Не удалось скачать установщик Docker с https://get.docker.com.
  Проверьте интернет и повторите, либо поставьте Docker вручную:
    https://docs.docker.com/engine/install/"
    fi
    if [ ! -s "$script_path" ]; then
        rm -f "$script_path"
        die "Установщик Docker скачался пустым. Поставьте Docker вручную: https://docs.docker.com/engine/install/"
    fi

    say "Выполняю: $SUDO sh $script_path"
    if ! $SUDO sh "$script_path"; then
        rm -f "$script_path"
        die "Установщик Docker завершился с ошибкой (сообщения выше).
  Поставьте Docker вручную: https://docs.docker.com/engine/install/"
    fi
    rm -f "$script_path"

    current_user=${USER:-$(id -un 2>/dev/null || echo user)}
    say "Выполняю: $SUDO usermod -aG docker $current_user"
    if ! $SUDO usermod -aG docker "$current_user"; then
        warn "Не удалось добавить $current_user в группу docker — запускайте docker через sudo."
    fi

    cat <<TEXT

Docker установлен. Чтобы журнал заработал, осталось два шага:

  1. Перезайдите в систему (или выполните  newgrp docker ) — иначе docker
     не пустит вас без sudo: новая группа действует только в новой сессии.
  2. Запустите установку заново:  bash install.sh

Проверить, что Docker работает:  docker run --rm hello-world
TEXT
    exit 0
}

#: Единая обработка «Docker не найден» / «нет docker compose».
#: $1 — чего не хватает: "docker" или "docker compose".
handle_missing_docker() {
    missing=$1
    os=$(detect_os)

    say ""
    if [ "$missing" = "docker" ]; then
        warn "Docker не найден."
        say "Он нужен, чтобы запустить журнал в контейнерах: PostgreSQL и само приложение."
    else
        warn "Docker найден, но нет «docker compose»."
        say "Плагин compose нужен, чтобы одной командой поднять базу и приложение."
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

#: Linux: решаем по /etc/os-release, можно ли ставить Docker автоматически.
handle_missing_docker_linux() {
    read_linux_distro
    family=$(linux_family)
    say ""
    describe_linux

    if ! linux_supported "$family"; then
        print_unsupported_linux
        exit 1
    fi

    say "Буду ставить Docker официальным скриптом get.docker.com."

    if [ "$NO_PROMPT" -eq 1 ]; then
        say ""
        say "Флаг --no-prompt: вопросов не задаю, ничего не устанавливаю."
        print_linux_install_hint
        exit 1
    fi

    if [ "$INSTALL_DOCKER" -eq 1 ]; then
        say "Флаг --install-docker: ставлю Docker без вопроса."
    else
        if [ ! -t 0 ]; then
            say ""
            say "Ввод недоступен (нет терминала): вопрос не задаю, ничего не устанавливаю."
            say "Для автоматической установки запустите с флагом --install-docker."
            print_linux_install_hint
            exit 1
        fi

        printf '\n%sУстановить сейчас?%s [y/N] ' "$BOLD" "$RESET"
        answer=''
        IFS= read -r answer || answer=''
        case "$answer" in
            y | Y | yes | YES | Yes | д | да | Да | Д) ;;
            *)
                say ""
                say "Установка отменена — на этой машине ничего не изменено."
                print_linux_install_hint
                exit 1
                ;;
        esac
    fi

    install_docker_linux
}

# --- Начало -------------------------------------------------------------------

printf '\n%sЭлектронный журнал посещаемости — установка%s\n' "$BOLD" "$RESET"
printf '%sDjango 5 + PostgreSQL + Docker · каталог: %s%s\n' "$DIM" "$SCRIPT_DIR" "$RESET"

# --- 1. Docker и docker compose ----------------------------------------------

step "Проверяю Docker"

if ! command -v docker >/dev/null 2>&1; then
    handle_missing_docker "docker"
fi

if ! docker info >/dev/null 2>&1; then
    die "Docker установлен, но его служба не отвечает.
  Запустите Docker Desktop (или: sudo systemctl start docker) и повторите запуск.
  Проверить самому: docker info"
fi
ok "Docker: $(docker --version 2>/dev/null || echo 'есть')"

COMPOSE_KIND="v2"
if docker compose version >/dev/null 2>&1; then
    COMPOSE_KIND="v2"
    ok "docker compose: $(docker compose version --short 2>/dev/null || echo 'есть')"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_KIND="v1"
    warn "Найден устаревший docker-compose (v1). Работать будет, но лучше поставить docker compose v2."
    ok "docker-compose: $(docker-compose --version 2>/dev/null || echo 'есть')"
else
    handle_missing_docker "docker compose"
fi

# --- 2. Файл .env -------------------------------------------------------------

step "Готовлю настройки (.env)"

if [ -f "$ENV_FILE" ]; then
    ok "Файл .env уже есть — оставляю как есть."
    note "Секреты и настройки не пересоздаются: повторный запуск их не меняет."
else
    if [ ! -f "$ENV_TEMPLATE" ]; then
        die "Нет файла $ENV_TEMPLATE — скачайте проект целиком (в нём лежит шаблон настроек)."
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

    # На всякий случай: без ключа нужной длины журнал не стартует.
    if [ "${#secret_key}" -lt 50 ] || [ "${#db_password}" -lt 16 ]; then
        rm -f "$ENV_FILE"
        die "Не удалось сгенерировать случайные значения (нет openssl, python3 и /dev/urandom).
  Создайте .env вручную из .env.example и заполните SECRET_KEY и POSTGRES_PASSWORD."
    fi
    ok "Файл .env создан из $ENV_TEMPLATE."
    note "SECRET_KEY и POSTGRES_PASSWORD сгенерированы случайно и сохранены только в .env."
fi

# Обязательные значения: без них контейнеры не поднимутся, лучше сказать сразу.
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
    die "В файле .env не заданы:$missing
  Откройте .env и заполните их (подсказки — в .env.example) или удалите .env
  и запустите install.sh снова: он создаст файл со случайными секретами."
fi
if [ "${#secret_key}" -lt 50 ]; then
    die "SECRET_KEY в .env короче 50 символов — журнал так не запустится.
  Сгенерируйте новый ключ: openssl rand -base64 64 | tr -dc 'A-Za-z0-9' | cut -c1-64
  Внимание: со сменой SECRET_KEY все пользователи выйдут из журнала."
fi
ok "Обязательные настройки на месте (DEBUG=$debug, ALLOWED_HOSTS=$allowed_hosts)."

# --- 3. Конфигурация compose -------------------------------------------------

step "Проверяю конфигурацию"

if ! compose config -q; then
    die "docker compose не принял конфигурацию (сообщение выше).
  Обычно это незаполненная или неверная переменная в .env — сравните её с .env.example."
fi
ok "Конфигурация верная."

# --- 4. Сборка и запуск ------------------------------------------------------

step "Собираю и запускаю контейнеры"
note "Первая сборка занимает несколько минут: скачиваются образы и ставятся библиотеки."
if ! compose up -d --build; then
    die "Контейнеры не запустились.
  Посмотрите причину: docker compose logs --tail=100 $SERVICE
  Частые случаи (нет зеркала Docker Hub, занят порт 8000) разобраны в README,
  раздел «Если что-то пошло не так»."
fi
ok "Контейнеры запущены (db и $SERVICE)."

# --- 5. Ожидание готовности --------------------------------------------------

step "Жду готовности журнала"
note "Самопроверка каждые ${HEALTH_INTERVAL} с, максимум ${HEALTH_TIMEOUT} с."

started=$(date +%s)
not_running=0
ready=0
while [ "$ready" -eq 0 ]; do
    if output=$(compose exec -T "$SERVICE" python manage.py healthcheck 2>&1); then
        ready=1
        break
    fi

    case "$output" in
        *"is not running"* | *"not running"* | *"не запущен"*)
            not_running=$((not_running + 1))
            ;;
        *)
            not_running=0
            ;;
    esac
    if [ "$not_running" -ge 3 ]; then
        printf '\n'
        warn "Контейнер $SERVICE не работает — журнал не поднимется."
        compose logs --tail=40 "$SERVICE" || true
        die "Журнал не запустился.
  Смотрите полные логи: docker compose logs $SERVICE
  Причину чаще всего видно в первых строках после «==> Применяю миграции…»."
    fi

    now=$(date +%s)
    if [ $((now - started)) -ge "$HEALTH_TIMEOUT" ]; then
        printf '\n'
        warn "Журнал не ответил за ${HEALTH_TIMEOUT} с."
        compose ps || true
        compose logs --tail=60 "$SERVICE" || true
        die "Журнал не поднялся за ${HEALTH_TIMEOUT} с.
  Логи: docker compose logs $SERVICE
  Если сборка долго скачивает образы — запустите снова, прогресс сохраняется.
  Другой таймаут: HEALTH_TIMEOUT=900 bash install.sh"
    fi

    printf '.'
    sleep "$HEALTH_INTERVAL"
done
printf '\n'
ok "Журнал отвечает, самопроверка без ошибок."

# --- 6. Итог -----------------------------------------------------------------

port=$(env_value WEB_PORT)
case "$port" in
    '' | *[!0-9]*) port=8000 ;;
esac
url="http://localhost:$port"

line='=============================================================================='
printf '\n%s%s%s\n' "$BOLD" "$line" "$RESET"
printf '%s  ЖУРНАЛ ЗАПУЩЕН%s\n' "$BOLD" "$RESET"
printf '%s%s%s\n' "$BOLD" "$line" "$RESET"

cat <<TEXT

  Адрес:            $url
  Админка Django:   $url/admin/
  Демо-страница:    $url/demo/   (появляется при DEMO_MODE=1)

  ПЕРВОЕ ОТКРЫТИЕ — МАСТЕР НАСТРОЙКИ
  Откройте адрес в браузере: журнал сам покажет страницу первичной настройки
  (/setup/). Там создаётся главный администратор: логин, имя и пароль, название
  журнала и, по желанию, логотип. После этого мастер больше не появляется,
  а вход выполняется по созданному логину на странице $url/login/.

  ДЕМО-РЕЖИМ (вход без пароля, отдельная демонстрационная база)
  1. В файле .env поставьте DEMO_MODE=1
  2. Перезапустите: bash install.sh   (или: docker compose up -d)
  3. Откройте $url/demo/ и нажмите «Войти как методист»
     или «Войти как преподаватель».
  Демо-данные лежат в отдельной базе и рабочий журнал не затрагивают.
  Перед реальной работой верните DEMO_MODE=0.

  ДАННЫЕ И РЕЗЕРВНЫЕ КОПИИ
  База и загруженные файлы лежат в томах postgres_data и media_data и
  сохраняются при обновлении и остановке. Копия всех данных — раздел
  «Резервная копия» в журнале (только для администратора).

  ПОЛЕЗНЫЕ КОМАНДЫ (в каталоге проекта)
    docker compose logs -f $SERVICE          — смотреть логи
    docker compose ps                   — состояние контейнеров
    docker compose down                 — остановить (данные сохраняются)
    docker compose up -d --build        — запустить после замены файлов

  Настройки — в файле .env, подробности — в README.md
  и docs/admin-guide.md.

TEXT
