#!/usr/bin/env bash
# Автовыкладка: раз в 5 секунд проверяет origin/main и при новом коммите пересобирает сервис.
# Отчёт (контейнеры, health, логи сборки и приложения) уходит в ПРИВАТНЫЙ репозиторий из REPORT_REPO в .env,
# если есть ключ .secrets/deploy_key — deploy key этого репозитория с правом записи. Без них отчёт не отправляется.
#
# Запуск:         tmux new -s bsdeploy 'bash deploy/autodeploy.sh'
# Разовый отчёт:  bash deploy/autodeploy.sh report
# Остановить:     Ctrl+C. Работающий сервис при этом не останавливается.
set -uo pipefail

LAST_SIG=""
REPORT_FAILED=0

say() { echo "$(date '+%H:%M:%S') $*" | tee -a .report/autodeploy.log; }

env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2-; }

deploy() {
  say "выкладываю $(git log -1 --format='%h %s')"
  if GIT_SHA="$(git rev-parse --short HEAD)" docker compose up -d --build --remove-orphans 2>&1 | tee .report/deploy.log; then
    say "готово"
  else
    say "ОШИБКА выкладки — работает предыдущая версия, подробности в deploy.log"
  fi
}

report() {
  local key="$PWD/.secrets/deploy_key" known="$PWD/.secrets/known_hosts" dir=".report/out" repo domain port sig
  repo=$(env_value REPORT_REPO)
  [ -n "$repo" ] && [ -f "$key" ] || return 0
  domain=$(env_value DOMAIN)
  port=$(env_value APP_PORT)
  rm -rf "$dir" && mkdir -p "$dir"
  docker compose ps -a >"$dir/ps.txt" 2>&1
  {
    echo "local:  $(curl -sS -m 5 "http://127.0.0.1:${port:-18080}/health" 2>&1)"
    [ -n "$domain" ] && echo "public: https://$domain/health -> $(curl -sS -m 10 -o /dev/null -w '%{http_code}' "https://$domain/health" 2>&1)"
  } >"$dir/health.txt"
  docker compose logs --no-color --tail 300 app >"$dir/app.log" 2>&1
  docker compose logs --no-color --tail 200 caddy >"$dir/caddy.log" 2>&1
  cp .report/deploy.log .report/autodeploy.log "$dir/" 2>/dev/null
  sig=$(cat "$dir"/* | md5sum)
  [ "$sig" = "$LAST_SIG" ] && [ "${1:-}" != force ] && return 0
  echo "$(date '+%F %T %Z') · $(hostname) · $(git log -1 --format='%h %s')" >"$dir/status.txt"
  if (
    cd "$dir" && git init -q && git add -A &&
      git -c user.name=bs-server -c user.email=bs-server@localhost commit -q -m "отчёт $(date '+%F %T')" &&
      GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$known" \
        git push -qf "$repo" HEAD:main
  ); then
    LAST_SIG=$sig
    REPORT_FAILED=0
  elif [ "$REPORT_FAILED" = 0 ]; then
    REPORT_FAILED=1
    say "не удалось отправить отчёт в GitHub — добавлен ли deploy key с правом записи?"
  fi
}

main() {
  cd "$(dirname "$0")/.." || exit 1
  mkdir -p .report
  if [ "${1:-}" = report ]; then
    report force
    exit
  fi
  local branch="${BRANCH:-main}" tick=0
  deploy
  report force
  while sleep 5; do
    if git fetch -q origin "$branch" && [ "$(git rev-parse HEAD)" != "$(git rev-parse "origin/$branch")" ]; then
      git reset -q --hard "origin/$branch"  # .env, ключ и данные не в git — не трогаются
      deploy
      report force
      tick=0
    elif (( ++tick % 6 == 0 )); then
      report  # раз в 30 секунд, если что-то изменилось
    fi
  done
}

# Весь код внутри функции: bash прочитает его целиком, и обновление этого файла через git не сломает цикл
main "$@"
exit
