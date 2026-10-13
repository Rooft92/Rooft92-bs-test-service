#!/usr/bin/env bash
# Автовыкладка: раз в 5 секунд проверяет origin/main и при новом коммите пересобирает сервис.
# Запуск:     tmux new -s bsdeploy 'bash deploy/autodeploy.sh'
# Остановить: Ctrl+C в этом окне. Работающий сервис при этом не останавливается.
set -u

deploy() {
  echo "$(date '+%H:%M:%S') выкладываю $(git log -1 --format='%h %s')"
  if GIT_SHA="$(git rev-parse --short HEAD)" docker compose up -d --build --remove-orphans; then
    echo "$(date '+%H:%M:%S') готово"
  else
    echo "$(date '+%H:%M:%S') ОШИБКА выкладки — работает предыдущая версия"
  fi
}

main() {
  cd "$(dirname "$0")/.." || exit 1
  local branch="${BRANCH:-main}"
  deploy
  while sleep 5; do
    git fetch -q origin "$branch" || continue
    if [ "$(git rev-parse HEAD)" != "$(git rev-parse "origin/$branch")" ]; then
      git reset -q --hard "origin/$branch"  # .env и данные не в git — не трогаются
      deploy
    fi
  done
}

# Весь код внутри функции: bash прочитает его целиком, и обновление этого файла через git не сломает цикл
main "$@"
exit
