#!/usr/bin/env bash
# Загрузить образ из GHCR и обновить приложение, сохранив том с данными.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Использование: ./scripts/deploy.sh [ghcr.io/owner/repository:latest]

Без аргумента образ берётся из KLOP_IMAGE в .env или окружении.
Скрипт можно запускать из любого каталога; используется проект рядом со скриптом.
KLOP_ENV_FILE позволяет указать другой файл окружения (абсолютный путь).
Для приватного образа предварительно выполните docker login ghcr.io.
EOF
}

die() { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }
if [[ ${1:-} == -h || ${1:-} == --help ]]; then usage; exit 0; fi
(( $# <= 1 )) || { usage >&2; exit 1; }

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
command -v docker >/dev/null || die 'Не найден Docker.'
command -v flock >/dev/null || die 'Не найден flock (пакет util-linux).'
[[ -f $project_dir/compose.ghcr.yaml ]] || die 'Рядом со скриптом нет compose.ghcr.yaml.'

# Защищает от одновременных запусков, в том числе из cron.
exec 9>"$project_dir/.deploy.lock"
if ! flock -n 9; then
    printf 'Другое обновление уже выполняется.\n'
    exit 0
fi

docker compose version >/dev/null || die 'Нужен Docker Compose v2 или новее.'
docker info >/dev/null || die 'Нет доступа к работающему Docker daemon.'

compose=(docker compose --project-directory "$project_dir" -f "$project_dir/compose.ghcr.yaml")
env_file=${KLOP_ENV_FILE:-$project_dir/.env}
if [[ -n ${KLOP_ENV_FILE:-} || -f $env_file ]]; then
    [[ -f $env_file ]] || die "Не найден файл окружения: $env_file"
    compose+=(--env-file "$env_file")
fi
if (( $# == 1 )); then
    export KLOP_IMAGE=$1
fi

image=$("${compose[@]}" config --images)
[[ $image =~ ^ghcr\.io/[a-z0-9][a-z0-9._/-]*(:[a-zA-Z0-9_][a-zA-Z0-9_.-]*|@sha256:[a-f0-9]{64})?$ ]] \
    || die 'Нужен один образ вида ghcr.io/owner/repository:latest.'
"${compose[@]}" config --quiet

printf 'Загрузка %s…\n' "$image"
"${compose[@]}" pull --policy always klop
printf 'Обновление контейнера…\n'
"${compose[@]}" up --detach --no-build --pull never --wait --wait-timeout 120 klop
"${compose[@]}" ps klop
printf 'Готово. Данные и скрытый путь сохранены в существующем томе.\n'
