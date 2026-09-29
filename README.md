# 🌸 Бот оголошень про продаж букетів (Рівне)

Telegram-бот приймає оголошення про продаж букетів, приймає квитанцію про оплату розміщення,
передає все адмінам на перевірку і після підтвердження автоматично публікує пост у канал.

## Структура проєкту

```
.
├── bot.py               # точка входу
├── config.py            # читання .env
├── db.py                # SQLite (aiosqlite)
├── filters.py           # фільтр адміна IsAdmin
├── keyboards.py         # клавіатури та CallbackData
├── services.py          # надсилання оголошень, модерація, публікація
├── states.py            # стани FSM
├── texts.py             # усі тексти бота
├── utils.py             # форматування поста, парсинг ціни та ніку
├── handlers/
│   ├── __init__.py
│   ├── admin.py         # модерація, /pending
│   └── user.py          # анкета з 7 кроків, оплата
├── requirements.txt
├── .env.example
├── Dockerfile
└── docker-compose.yml
```

## 1. Створення бота через @BotFather

1. Відкрийте [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Вкажіть назву та username бота (наприклад, `shche_kvitne_bot`).
3. Скопіюйте токен вигляду `1234567890:AA...` — це `BOT_TOKEN`.
4. (Необовʼязково) `/setdescription`, `/setuserpic` — опис і аватар.

## 2. Як дізнатися свій user_id (ADMIN_IDS)

- Напишіть боту [@userinfobot](https://t.me/userinfobot) — він відповість вашим `Id`.
- Кілька адмінів вказуються через кому: `ADMIN_IDS=111111111,222222222`.
- **Кожен адмін має хоча б раз натиснути `/start` у вашому боті**, інакше бот не зможе йому писати.

## 3. Додавання бота адміном у канал

1. Канал → «Керування каналом» → «Адміністратори» → «Додати адміністратора».
2. Знайдіть свого бота за username.
3. Увімкніть право **«Публікація повідомлень»** і збережіть.

## 4. Як дізнатися CHANNEL_ID

- **Публічний канал:** можна просто вказати `CHANNEL_ID=@назва_каналу`.
- **Приватний канал** (як у вас, з посиланням `t.me/+...`):
  1. Перешліть будь-який пост із каналу боту [@userinfobot](https://t.me/userinfobot)
     або [@getidsbot](https://t.me/getidsbot).
  2. Він покаже id вигляду `-1001234567890` — це і є `CHANNEL_ID`.
  3. Альтернатива: відкрийте канал у [web.telegram.org](https://web.telegram.org/a/) —
     в адресному рядку буде `#-1001234567890`.

## 5. Налаштування `.env`

Скопіюйте `.env.example` у `.env` і заповніть значення:

```bash
cp .env.example .env      # Linux / macOS
copy .env.example .env    # Windows
```

## 6. Локальний запуск

Потрібен Python 3.11+.

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux / macOS:
source venv/bin/activate

pip install -r requirements.txt
python bot.py
```

База `ads.db` створиться автоматично.

## 7. Запуск на VPS

### Варіант А — systemd

```bash
sudo apt update && sudo apt install -y python3 python3-venv git
sudo mkdir -p /opt/kvity-bot && sudo chown $USER /opt/kvity-bot
# скопіюйте файли проєкту в /opt/kvity-bot (git clone / scp)
cd /opt/kvity-bot
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
```

Створіть файл `/etc/systemd/system/kvity-bot.service`:

```ini
[Unit]
Description=Kvity Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/opt/kvity-bot
ExecStart=/opt/kvity-bot/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Замініть `YOUR_USER` на свого користувача і запустіть:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kvity-bot
sudo systemctl status kvity-bot
journalctl -u kvity-bot -f     # логи
```

### Варіант Б — Docker

```bash
cp .env.example .env && nano .env
docker compose up -d --build
docker compose logs -f
```

База зберігається в папці `./data` на хості і не зникає при перезапуску контейнера.

## Як працює бот

1. Користувач натискає «🌸 Виставити букет» і проходить 7 кроків (фото → назва → ціна →
   стара ціна → адреса → коли отримано → нік). Скасувати можна кнопкою «❌ Скасувати» або `/cancel`.
2. Після 7-го кроку оголошення зберігається зі статусом `awaiting_payment`,
   користувач бачить превʼю і реквізити.
3. Користувач надсилає квитанцію (фото / PDF) → статус `pending_review`, усі адміни отримують
   фото, квитанцію та картку з кнопками «✅ Опублікувати» / «❌ Відхилити».
4. Після публікації статус `published`, автор отримує посилання на пост.
   При відхиленні адмін пише причину (або `-`), статус `rejected`, автор отримує повідомлення.

Команди адміна: `/pending` — список оголошень на перевірці (з кнопками, щоб відкрити кожне).

## Примітки

- Стан анкети зберігається в памʼяті (MemoryStorage): після перезапуску бота незавершені
  анкети скидаються, але вже збережені оголошення лишаються в базі.
- Якщо публікація в канал не вдалася (бот не адмін, неправильний `CHANNEL_ID`) — адмін
  отримає текст помилки, статус оголошення не зміниться, і можна натиснути «Опублікувати» ще раз.
