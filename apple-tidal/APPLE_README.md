# Получение Apple Music User Token (MUT)

`.p8` + `APPLE_TEAM_ID` + `APPLE_KEY_ID` дают только **developer token** (серверный JWT).
**Music User Token (MUT)** — это пользовательская авторизация поверх него, которую Apple выдаёт
**только через MusicKit JS в браузере** после входа в аккаунт с активной подпиской Apple Music.

## Что делает helper (`music_service/auth_helper/server.py`)

`python -m music_service.cli auth apple` →
1. строит developer token из твоих `APPLE_TEAM_ID` / `APPLE_KEY_ID` / `.p8`;
2. поднимает локальный сервер на `http://127.0.0.1:8770` со страницей MusicKit JS;
3. открывает браузер → кнопка **"Authorize Apple Music"** → вход в аккаунт с подпиской;
4. ловит MUT на `POST /save-token` → сохраняет в `~/.hermes/music_tools/apple_tokens.json` и печатает путь.

Чтение токена (`MusicUserTokenStore.resolve`): env `APPLE_MUSIC_USER_TOKEN` имеет приоритет,
иначе берётся из файла-стора.

## Важно для fleet-сетапа

Внутри контейнера браузера и опубликованного порта нет → helper там не отработает.
Запускай его **на хосте**, потом отдай токен контейнеру.

### Шаги (PowerShell, на хосте)

```powershell
cd D:\work\hermes-tools\apple-tidal
# в .env должны быть APPLE_TEAM_ID / APPLE_KEY_ID / APPLE_PRIVATE_KEY_PATH
.\.venv\Scripts\python -m music_service.cli auth apple
```

Откроется `http://127.0.0.1:8770` → **Authorize Apple Music** → войди под аккаунтом с активной
подпиской → токен сохранится и распечатается. Забрать значение:

```powershell
Get-Content $HOME\.hermes\music_tools\apple_tokens.json
```

### Отдать контейнеру

- **Проще всего через dashboard:** скопируй `music_user_token` в поле `APPLE_MUSIC_USER_TOKEN`
  в форме установки `music` → оно запишется в `.env` контейнера (env побеждает в `resolve`).
- **Альтернатива:** положить сам `apple_tokens.json` в том:
  `<CLIENTS_ROOT>/<id>/.hermes/music_tools/apple_tokens.json` (внутри это `/opt/data/...`, т.к. `HOME=/opt/data`).

## Нюансы

- Нужна **активная подписка Apple Music** на аккаунте, которым логинишься (developer-аккаунта мало).
- `APPLE_MUSIC_STOREFRONT` должен совпадать со страной аккаунта.
- MUT долгоживущий, но отзываемый — если поймаешь `403`, просто перезапусти `auth apple`.
