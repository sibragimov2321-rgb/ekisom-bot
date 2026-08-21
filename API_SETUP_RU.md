# Настройка API БК в Railway

Ключи и пароли нельзя хранить в GitHub. Добавляйте их только в Railway:

`Service → Variables → New Variable`.

## Безопасное включение автозачисления

Сначала добавьте и проверьте ключи API с настройкой ниже. При значении `false`
бот проверяет игровой ID, но не делает зачисление в БК. После теста ID и чека
можно отдельно включить автозачисление:

```env
BOOKMAKER_API_AUTO_CREDIT_ENABLED=false
```

Чтобы разрешить зачисление только после подтверждения чека администратором,
измените значение на `true` в Railway. Это значение не следует добавлять в
GitHub вместе с ключами.

## 1XBET

Добавьте эти переменные:

```env
XBET_API_BASE_URL=https://partners.servcul.com/CashdeskBotAPI
XBET_API_HASH=
XBET_CASHIER_PASSWORD=
XBET_CASHDESK_ID=
XBET_LOGIN=
XBET_ALLOWED_CURRENCY_IDS=
```

## MELBET

```env
MELBET_API_BASE_URL=https://partners.servcul.com/CashdeskBotAPI
MELBET_API_HASH=
MELBET_CASHIER_PASSWORD=
MELBET_CASHDESK_ID=
MELBET_LOGIN=
MELBET_ALLOWED_CURRENCY_IDS=
```

## 1WIN

```env
ONEWIN_API_BASE_URL=https://partners.servcul.com/CashdeskBotAPI
ONEWIN_API_HASH=
ONEWIN_CASHIER_PASSWORD=
ONEWIN_CASHDESK_ID=
ONEWIN_LOGIN=
ONEWIN_ALLOWED_CURRENCY_IDS=
```

## Как это работает

- Если переменные пустые, бот работает вручную.
- Если переменные заполнены, бот проверяет игровой ID через API.
- При `BOOKMAKER_API_AUTO_CREDIT_ENABLED=true` после подтверждения чека админом бот отправляет депозит в API БК.
- `ALLOWED_CURRENCY_IDS` можно оставить пустым, если пока не знаете ID валюты. Если знаете — укажите через запятую, например `1,2`.

После добавления или изменения переменных Railway сам перезапустит сервис. Если не перезапустил — нажмите `Redeploy`.
