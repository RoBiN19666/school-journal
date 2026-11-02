# =============================================================================
#  Электронный журнал посещаемости — установка одной командой (Windows)
# =============================================================================
#  Запуск в PowerShell (из папки проекта):
#      powershell -ExecutionPolicy Bypass -File .\install.ps1
#
#  Что делает:
#    1. проверяет, что есть Docker и docker compose; если Docker нет —
#       объясняет, что Docker Desktop для Windows ставится вручную
#       (это большое приложение с графическим мастером, платной лицензией
#       для крупных организаций и перезагрузкой), и выходит. Сам установщик
#       ничего не скачивает и не ставит;
#    2. создаёт .env из .env.example (если .env ещё нет) со случайными
#       SECRET_KEY и POSTGRES_PASSWORD и рабочими настройками по умолчанию;
#    3. собирает и запускает контейнеры: docker compose up -d --build;
#    4. ждёт, пока журнал ответит на самопроверку (manage.py healthcheck);
#    5. печатает адрес журнала и что делать при первом открытии.
#
#  Повторный запуск безопасен: существующий .env не переписывается, секреты
#  не пересоздаются, данные в томах не трогаются.
# =============================================================================

[CmdletBinding()]
param(
    # Сколько секунд ждать готовности журнала.
    [int]$HealthTimeout = 300,
    # Пауза между проверками готовности, секунд.
    [int]$HealthInterval = 4
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$EnvFile = Join-Path (Get-Location) '.env'
$EnvTemplate = Join-Path (Get-Location) '.env.example'
$Service = 'web'

# --- Оформление вывода -------------------------------------------------------

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
    Write-Host "  [ОШИБКА] $Text" -ForegroundColor Red
    Write-Host ''
    exit 1
}

# Сообщение «Docker Desktop ставится вручную». Никакой установки и вопросов:
# так решили осознанно — см. README, раздел «Если Docker не установлен».
function Show-DockerManualMessage([string]$Missing) {
    Write-Host ''
    if ($Missing -eq 'docker') {
        Write-Warn 'Docker не найден.'
        Write-Host 'Docker нужен, чтобы запустить журнал в контейнерах: PostgreSQL и само приложение.'
    }
    else {
        Write-Warn "Docker есть, но нет 'docker compose'."
        Write-Host 'Плагин compose нужен, чтобы одной командой поднять базу и приложение.'
    }
    Write-Host @"

Docker Desktop для Windows ставится вручную — установщик журнала его сам
не ставит и ничего не скачивает. Причины:
  * это большое приложение с графическим мастером настройки;
  * после установки требуется перезагрузка системы;
  * лицензия Docker Desktop для крупных организаций платная —
    решение об установке должно остаться за вами.

  Страница загрузки:    https://www.docker.com/products/docker-desktop/
  Установка на Windows: https://docs.docker.com/desktop/install/windows-install/

После установки запустите Docker Desktop, дождитесь «Engine running»
и повторите установку журнала:
  powershell -ExecutionPolicy Bypass -File .\install.ps1
"@
    exit 1
}

# --- Docker ------------------------------------------------------------------

# Вызов docker с предсказуемой обработкой кода возврата: в Windows PowerShell
# вывод docker в stderr не должен превращаться в исключение.
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

# --- Файл .env ---------------------------------------------------------------

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
    # UTF-8 без BOM и переводы строк LF: docker compose читает .env построчно,
    # и «\r» в конце значения попал бы в пароль базы.
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

# --- Начало ------------------------------------------------------------------

Write-Host ''
Write-Host 'Электронный журнал посещаемости — установка' -ForegroundColor White
Write-Host "Django 5 + PostgreSQL + Docker · каталог: $PSScriptRoot" -ForegroundColor DarkGray

# --- 1. Docker и docker compose ----------------------------------------------

Write-Step 'Проверяю Docker'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Show-DockerManualMessage 'docker'
}

$info = Invoke-Docker -Arguments @('info') -Capture
if ($info.Code -ne 0) {
    Fail @"
Docker установлен, но его служба не отвечает.
  Запустите Docker Desktop и дождитесь, пока он напишет «Engine running».
  Проверить самому: docker info
"@
}
$dockerVersion = (Invoke-Docker -Arguments @('--version') -Capture).Output.Trim()
Write-Ok "Docker: $dockerVersion"

$composeVersion = Invoke-Docker -Arguments @('compose', 'version', '--short') -Capture
if ($composeVersion.Code -ne 0) {
    Show-DockerManualMessage 'compose'
}
Write-Ok "docker compose: $($composeVersion.Output.Trim())"

# --- 2. Файл .env -------------------------------------------------------------

Write-Step 'Готовлю настройки (.env)'

if (Test-Path -LiteralPath $EnvFile) {
    Write-Ok 'Файл .env уже есть — оставляю как есть.'
    Write-Note 'Секреты и настройки не пересоздаются: повторный запуск их не меняет.'
}
else {
    if (-not (Test-Path -LiteralPath $EnvTemplate)) {
        Fail "Нет файла .env.example — скачайте проект целиком (в нём лежит шаблон настроек)."
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
        Fail 'Не удалось сгенерировать случайные значения. Создайте .env вручную из .env.example.'
    }
    Write-Ok 'Файл .env создан из .env.example.'
    Write-Note 'SECRET_KEY и POSTGRES_PASSWORD сгенерированы случайно и сохранены только в .env.'
}

# Обязательные значения: без них контейнеры не поднимутся, лучше сказать сразу.
$missing = @()
if (-not (Get-EnvValue 'SECRET_KEY')) { $missing += 'SECRET_KEY' }
if (-not (Get-EnvValue 'POSTGRES_PASSWORD')) { $missing += 'POSTGRES_PASSWORD' }
if (-not (Get-EnvValue 'DEBUG')) { $missing += 'DEBUG' }
if (-not (Get-EnvValue 'ALLOWED_HOSTS')) { $missing += 'ALLOWED_HOSTS' }
if ($missing.Count -gt 0) {
    Fail @"
В файле .env не заданы: $($missing -join ', ')
  Откройте .env и заполните их (подсказки — в .env.example) или удалите .env
  и запустите install.ps1 снова: он создаст файл со случайными секретами.
"@
}
$secretKey = Get-EnvValue 'SECRET_KEY'
if ($secretKey.Length -lt 50) {
    Fail @"
SECRET_KEY в .env короче 50 символов — журнал так не запустится.
  Сгенерируйте новый ключ (в PowerShell):
    -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 64 | % {[char]$_})
  Внимание: со сменой SECRET_KEY все пользователи выйдут из журнала.
"@
}
Write-Ok "Обязательные настройки на месте (DEBUG=$(Get-EnvValue 'DEBUG'), ALLOWED_HOSTS=$(Get-EnvValue 'ALLOWED_HOSTS'))."

# --- 3. Конфигурация compose -------------------------------------------------

Write-Step 'Проверяю конфигурацию'

$config = Invoke-Docker -Arguments @('compose', 'config', '-q') -Capture
if ($config.Code -ne 0) {
    Write-Host $config.Output
    Fail @"
docker compose не принял конфигурацию (сообщение выше).
  Обычно это незаполненная или неверная переменная в .env — сравните её с .env.example.
"@
}
Write-Ok 'Конфигурация верная.'

# --- 4. Сборка и запуск ------------------------------------------------------

Write-Step 'Собираю и запускаю контейнеры'
Write-Note 'Первая сборка занимает несколько минут: скачиваются образы и ставятся библиотеки.'
$up = Invoke-Docker -Arguments @('compose', 'up', '-d', '--build')
if ($up.Code -ne 0) {
    Fail @"
Контейнеры не запустились.
  Посмотрите причину: docker compose logs --tail=100 web
  Частые случаи (нет доступа к Docker Hub, занят порт 8000) разобраны в README,
  раздел «Если что-то пошло не так».
"@
}
Write-Ok "Контейнеры запущены (db и $Service)."

# --- 5. Ожидание готовности --------------------------------------------------

Write-Step 'Жду готовности журнала'
Write-Note "Самопроверка каждые $HealthInterval с, максимум $HealthTimeout с."

$started = Get-Date
$notRunning = 0
$ready = $false
while (-not $ready) {
    $probe = Invoke-Docker -Arguments @('compose', 'exec', '-T', $Service, 'python', 'manage.py', 'healthcheck') -Capture
    if ($probe.Code -eq 0) {
        $ready = $true
        break
    }

    if ($probe.Output -match 'is not running|not running|не запущен') {
        $notRunning++
    }
    else {
        $notRunning = 0
    }
    if ($notRunning -ge 3) {
        Write-Host ''
        Write-Warn "Контейнер $Service не работает — журнал не поднимется."
        $null = Invoke-Docker -Arguments @('compose', 'logs', '--tail=40', $Service)
        Fail @"
Журнал не запустился.
  Смотрите полные логи: docker compose logs $Service
  Причину чаще всего видно в первых строках после «==> Применяю миграции…».
"@
    }

    if (((Get-Date) - $started).TotalSeconds -ge $HealthTimeout) {
        Write-Host ''
        Write-Warn "Журнал не ответил за $HealthTimeout с."
        $null = Invoke-Docker -Arguments @('compose', 'ps')
        $null = Invoke-Docker -Arguments @('compose', 'logs', '--tail=60', $Service)
        Fail @"
Журнал не поднялся за $HealthTimeout с.
  Логи: docker compose logs $Service
  Если сборка долго скачивает образы — запустите снова, прогресс сохраняется.
  Другой таймаут: powershell -ExecutionPolicy Bypass -File .\install.ps1 -HealthTimeout 900
"@
    }

    Write-Host '.' -NoNewline
    Start-Sleep -Seconds $HealthInterval
}
Write-Host ''
Write-Ok 'Журнал отвечает, самопроверка без ошибок.'

# --- 6. Итог -----------------------------------------------------------------

$port = Get-EnvValue 'WEB_PORT'
if (-not ($port -match '^\d+$')) { $port = '8000' }
$url = "http://localhost:$port"

Write-Host ''
Write-Host '==============================================================================' -ForegroundColor White
Write-Host '  ЖУРНАЛ ЗАПУЩЕН' -ForegroundColor Green
Write-Host '==============================================================================' -ForegroundColor White
Write-Host @"

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
  2. Перезапустите: powershell -ExecutionPolicy Bypass -File .\install.ps1
  3. Откройте $url/demo/ и нажмите «Войти как методист»
     или «Войти как преподаватель».
  Демо-данные лежат в отдельной базе и рабочий журнал не затрагивают.
  Перед реальной работой верните DEMO_MODE=0.

  ДАННЫЕ И РЕЗЕРВНЫЕ КОПИИ
  База и загруженные файлы лежат в томах postgres_data и media_data и
  сохраняются при обновлении и остановке. Копия всех данных — раздел
  «Резервная копия» в журнале (только для администратора).

  ПОЛЕЗНЫЕ КОМАНДЫ (в каталоге проекта)
    docker compose logs -f $Service     — смотреть логи
    docker compose ps                   — состояние контейнеров
    docker compose down                 — остановить (данные сохраняются)
    docker compose up -d --build        — запустить после замены файлов

  Настройки — в файле .env, подробности — в README.md
  и docs/admin-guide.md.

"@
