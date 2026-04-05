# tg-backup

Экспорт структуры Telegram-аккаунта (метаданные, без содержимого сообщений) в JSON.

## Что экспортируется

Для каждого диалога (чат/канал/группа/пользователь/бот):
- ID, тип, название, username
- Описание / био
- Количество участников, invite-ссылка
- Папки, статус архивации и мьюта
- Количество непрочитанных сообщений
- Для контактов: номер телефона, имя, фамилия

## Установка

```bash
pip install -r requirements.txt
```

Также необходима скомпилированная библиотека TDLib (`tdjson.dll` / `libtdjson.so`).

## Настройка

1. Получите `api_id` и `api_hash` на https://my.telegram.org
2. Скопируйте `.env.example` в `.env` и заполните:

```bash
cp .env.example .env
```

## Использование

### Базовый запуск (профиль default)

```bash
python main.py
```

При первом запуске TDLib запросит номер телефона и код подтверждения.
Сессия сохраняется в `td_data/default/` — последующие запуски автоматические.

### Несколько аккаунтов

Для каждого аккаунта используйте отдельный профиль:

```bash
python main.py -p personal
python main.py -p work
python main.py -p wife
```

Каждый профиль хранит свою сессию в `td_data/<profile>/`. После авторизации туда же сохраняется `profile.json` с ID, именем и номером телефона — чтобы было понятно, чей это аккаунт.

### Полный экспорт (с днями рождения)

```bash
python main.py --full
python main.py -p work --full
```

Медленнее — запрашивает полную информацию по каждому контакту.

### Экспорт в один файл

```bash
python main.py --single-file
```

По умолчанию экспорт разбивается по типам (`users.json`, `channels.json`, ...) в папку `tg-backup-YYYY-MM-DD/`. С `--single-file` всё записывается в один `tg-backup-YYYY-MM-DD.json`.

### Экспорт в другую директорию

```bash
python main.py -o /path/to/backups
python main.py -p work -o /path/to/backups --full
```

### Все параметры

| Параметр | Описание |
|---|---|
| `-p`, `--profile` | Имя профиля/сессии (по умолчанию `default`) |
| `--single-file` | Всё в один JSON вместо папки с файлами по типам |
| `--full` | Полная информация по контактам (медленнее) |
| `-o`, `--output-dir` | Директория для экспорта (по умолчанию `.`) |

## Структура экспорта

```
tg-backup-2026-04-04/
  meta.json        — дата экспорта, ID аккаунта, профиль
  folders.json     — папки Telegram
  users.json       — личные чаты
  bots.json        — боты
  groups.json      — базовые группы
  supergroups.json — супергруппы
  channels.json    — каналы
  contacts.json    — контакты без диалогов
```
