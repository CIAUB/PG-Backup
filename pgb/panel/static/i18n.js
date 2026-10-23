/* PG-Backup panel translations. Default language: English. */
(function (root) {
  "use strict";
  var D = {
    en: {
      _name: "English", _locale: "en-US", _dir: "ltr",
      "nav.dashboard": "Dashboard", "nav.backups": "Backups", "nav.schedulers": "Schedulers & Telegram", "nav.servers": "Servers & transfer",
      "nav.jobs": "Jobs & logs", "nav.security": "Security",
      "nav.convert": "Convert database", "cv.title": "Convert database engine", "cv.sub": "Create a converted copy of a backup for another database engine", "cv.safe": "The result is a NEW backup file. Nothing is restored and this server is not changed.", "cv.live": "Backup of this server", "cv.liveHint": "Takes a fresh backup of this server (read only), converts it and keeps only the converted file.",
      "sc.skipTest": "Skip the Telegram test message (save even if Telegram is unreachable from here)",
      "cv.target": "Convert to", "cv.liveBtn": "Backup and convert", "cv.files": "Backups in the folder", "cv.engine": "Current engine", "cv.none": "No conversion available", "cv.pairs": "Supported: TimescaleDB ⇄ PostgreSQL and MariaDB ⇄ MySQL. Download the converted file, copy it to the new server and restore it there.",
      "c.logout": "Sign out", "c.cancel": "Cancel", "c.close": "Close", "c.save": "Save", "c.delete": "Delete", "c.start": "Start", "c.stop": "Stop",
      "c.restart": "Restart", "c.back": "Back", "c.next": "Next", "c.done": "Done", "c.saved": "Saved", "c.emptyLog": "(empty)", "c.menu": "Menu",
      "c.theme": "Theme", "c.language": "Language", "th.system": "System", "th.light": "Light", "th.dark": "Dark",
      "st.done": "Succeeded", "st.failed": "Failed", "st.running": "Running", "st.active": "Active", "st.stopped": "Stopped", "st.none": "Not installed",
      "login.title": "Sign in", "login.sub": "Manage your PasarGuard backups", "login.user": "Username", "login.pass": "Password",
      "login.otp": "Authenticator code (or recovery code)", "login.otpPh": "123456 or recovery code", "login.submit": "Sign in",
      "login.tooMany": "Too many attempts. Try again in {s} seconds.",
      "dash.title": "Dashboard", "dash.sub": "Live status of this server and your backups", "dash.load": "System load", "dash.uptime": "Uptime: {v}",
      "dash.mem": "Memory", "dash.disk": "Disk (backup location)", "dash.free": "{v} free", "dash.backups": "Backups", "dash.latest": "latest: {v}",
      "dash.sched": "Schedulers", "dash.jobs": "Recent jobs", "dash.noSched": "No schedulers yet", "dash.noJobs": "No jobs yet", "dash.everyH": "every {n} h",
      "u.days": "{n} d", "u.hours": "{n} h", "u.minutes": "{n} min",
      "bk.title": "Backups", "bk.sub": "Create, download, restore and send PasarGuard backups", "bk.new": "New backup", "bk.node": "Include PG-Node",
      "bk.begin": "Start", "bk.upload": "Upload backup", "bk.uploaded": "Uploaded", "bk.file": "File", "bk.size": "Size", "bk.date": "Date",
      "bk.download": "Download", "bk.restore": "Restore", "bk.telegram": "Telegram", "bk.delTitle": "Delete backup", "bk.delBody": "Permanently delete {n}?",
      "bk.restoreTitle": "Restore backup", "bk.restoreWarn": "The current PasarGuard data (database and files) will be replaced with this backup, and services will stop for a while.",
      "bk.restoreType": "Type the exact file name to confirm:", "bk.none": "No backups yet",
      "sc.title": "Schedulers & Telegram", "sc.sub": "Automatic backup schedulers, bot settings and live logs",
      "sc.none": "No scheduler yet. Click “New scheduler” to send automatic backups to Telegram.", "sc.convert": "Convert to service", "sc.legacyNote": "This scheduler was made by an old version (token stored in the service file). Convert it to store the token safely and manage it here.", "sc.oldNote": "This scheduler runs outside systemd (screen/tmux) or is not installed. Convert it to a service to manage it here.", "sc.new": "New scheduler", "sc.name": "Name (optional)", "sc.create": "Create & start", "sc.created": "Scheduler created and started", "sc.newHint": "Create a bot with @BotFather, send it a message, then paste its token and your chat ID. A test message is sent first so mistakes are caught early.", "sc.removeQ": "Delete scheduler “{name}”? Backups stop; existing backup files are kept.", "sc.token": "Bot token", "sc.chat": "Chat ID",
      "sc.proxy": "Telegram proxy", "sc.proxyPh": "http://… or socks5h://… (empty = no proxy)", "sc.interval": "Interval (hours)", "sc.node": "Include PG-Node",
      "sc.tokenKeep": "Unchanged ({hint})", "sc.saveRestart": "Save & restart", "sc.test": "Send test message", "sc.logs": "Live logs", "sc.notInstalled": "Service not installed",
      "sv.title": "Servers & transfer", "sv.sub": "Move PasarGuard to a new server in one click", "sv.add": "Add server", "sv.name": "Name", "sv.address": "Address (IP / domain)",
      "sv.login": "Login", "sv.sshPort": "SSH port", "sv.user": "User", "sv.pass": "Password", "sv.key": "or SSH private key", "sv.hostfp": "Host key fingerprint (recommended; ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub)", "sv.note": "Credentials are stored encrypted and are never sent back to the browser.",
      "sv.authKey": "SSH key", "sv.authPw": "Password", "sv.transfer": "Transfer here", "sv.none": "No servers saved", "sv.transferTitle": "Transfer to {n}",
      "sv.transferWarn": "A fresh backup of this server is created and restored on the destination. PasarGuard data on the destination will be replaced.",
      "sv.confirmHost": "Type the destination address to confirm:", "sv.go": "Start transfer",
      "jb.title": "Jobs & logs", "jb.sub": "History of backup, restore, transfer and conversion operations", "jb.none": "No jobs have run yet", "jb.job": "Job #{n}", "jb.cancel": "Cancel job",
      "se.title": "Security", "se.sub": "Password, two-factor authentication and event log", "se.pw": "Change password", "se.pwOld": "Current password",
      "se.pwNew": "New password (min. 10 characters, mixed case and a digit)", "se.pwBtn": "Change password", "se.pwDone": "Password changed; other sessions were signed out",
      "se.tfa": "Two-factor authentication (TOTP)", "se.tfaDesc": "Works with Google Authenticator, Aegis, 2FAS or any TOTP app.", "se.enable": "Enable", "se.on": "Enabled",
      "se.recLeft": "Recovery codes left: {n}", "se.disable": "Disable", "se.curPw": "Password", "se.curCode": "Current authenticator code", "se.disabled": "Disabled",
      "se.scan": "Scan the QR code, or enter the key manually:", "se.code6": "6-digit code", "se.verify": "Verify & enable", "se.recTitle": "Recovery codes",
      "se.recWarn": "These codes are shown only once. Each works a single time. Keep them somewhere safe.", "se.recOk": "I saved them",
      "se.audit": "Event log", "se.time": "Time", "se.user": "User", "se.ip": "IP", "se.event": "Event", "se.details": "Details"
    },
    ru: {
      _name: "Русский", _locale: "ru-RU", _dir: "ltr",
      "nav.dashboard": "Обзор", "nav.backups": "Резервные копии", "nav.schedulers": "Планировщики и Telegram", "nav.servers": "Серверы и перенос",
      "nav.jobs": "Задачи и логи", "nav.security": "Безопасность",
      "nav.convert": "Конвертация БД", "cv.title": "Конвертация типа базы данных", "cv.sub": "Создайте копию бэкапа для другого типа базы данных", "cv.safe": "Результат — НОВЫЙ файл бэкапа. Ничего не восстанавливается, этот сервер не меняется.", "cv.live": "Бэкап этого сервера", "cv.liveHint": "Делает новый бэкап этого сервера (только чтение), конвертирует его и оставляет только конвертированный файл.",
      "sc.skipTest": "Не отправлять тестовое сообщение в Telegram (сохранить, даже если Telegram отсюда недоступен)",
      "cv.target": "Конвертировать в", "cv.liveBtn": "Бэкап и конвертация", "cv.files": "Бэкапы в папке", "cv.engine": "Текущий тип", "cv.none": "Конвертация недоступна", "cv.pairs": "Поддерживается: TimescaleDB ⇄ PostgreSQL и MariaDB ⇄ MySQL. Скачайте файл, перенесите на новый сервер и восстановите там.",
      "c.logout": "Выйти", "c.cancel": "Отмена", "c.close": "Закрыть", "c.save": "Сохранить", "c.delete": "Удалить", "c.start": "Запустить", "c.stop": "Остановить",
      "c.restart": "Перезапустить", "c.back": "Назад", "c.next": "Далее", "c.done": "Готово", "c.saved": "Сохранено", "c.emptyLog": "(пусто)", "c.menu": "Меню",
      "c.theme": "Тема", "c.language": "Язык", "th.system": "Системная", "th.light": "Светлая", "th.dark": "Тёмная",
      "st.done": "Успешно", "st.failed": "Ошибка", "st.running": "Выполняется", "st.active": "Активен", "st.stopped": "Остановлен", "st.none": "Не установлен",
      "login.title": "Вход", "login.sub": "Управление резервными копиями PasarGuard", "login.user": "Имя пользователя", "login.pass": "Пароль",
      "login.otp": "Код аутентификатора (или резервный код)", "login.otpPh": "123456 или резервный код", "login.submit": "Войти",
      "login.tooMany": "Слишком много попыток. Повторите через {s} с.",
      "dash.title": "Обзор", "dash.sub": "Состояние сервера и резервных копий в реальном времени", "dash.load": "Нагрузка", "dash.uptime": "Аптайм: {v}",
      "dash.mem": "Память", "dash.disk": "Диск (папка копий)", "dash.free": "свободно {v}", "dash.backups": "Копии", "dash.latest": "последняя: {v}",
      "dash.sched": "Планировщики", "dash.jobs": "Последние задачи", "dash.noSched": "Планировщиков пока нет", "dash.noJobs": "Задач пока нет", "dash.everyH": "каждые {n} ч",
      "u.days": "{n} дн.", "u.hours": "{n} ч", "u.minutes": "{n} мин",
      "bk.title": "Резервные копии", "bk.sub": "Создание, загрузка, восстановление и отправка копий PasarGuard", "bk.new": "Новая копия", "bk.node": "Включить PG-Node",
      "bk.begin": "Начать", "bk.upload": "Загрузить копию", "bk.uploaded": "Загружено", "bk.file": "Файл", "bk.size": "Размер", "bk.date": "Дата",
      "bk.download": "Скачать", "bk.restore": "Восстановить", "bk.telegram": "Telegram", "bk.delTitle": "Удалить копию", "bk.delBody": "Удалить {n} навсегда?",
      "bk.restoreTitle": "Восстановление копии", "bk.restoreWarn": "Текущие данные PasarGuard (база и файлы) будут заменены содержимым копии, службы на время остановятся.",
      "bk.restoreType": "Для подтверждения введите точное имя файла:", "bk.none": "Копий пока нет",
      "sc.title": "Планировщики и Telegram", "sc.sub": "Автоматические копии, настройки бота и логи в реальном времени",
      "sc.none": "Планировщиков пока нет. Нажмите «Новый планировщик», чтобы отправлять автобэкапы в Telegram.", "sc.convert": "Преобразовать в службу", "sc.legacyNote": "Этот планировщик создан старой версией (токен хранится в файле службы). Преобразуйте его, чтобы токен хранился безопасно и им можно было управлять здесь.", "sc.oldNote": "Этот планировщик работает вне systemd (screen/tmux) или не установлен. Преобразуйте его в службу, чтобы управлять им здесь.", "sc.new": "Новый планировщик", "sc.name": "Имя (необязательно)", "sc.create": "Создать и запустить", "sc.created": "Планировщик создан и запущен", "sc.newHint": "Создайте бота в @BotFather, напишите ему, затем вставьте токен и ваш chat ID. Сначала отправляется тестовое сообщение, чтобы сразу увидеть ошибки.", "sc.removeQ": "Удалить планировщик «{name}»? Бэкапы прекратятся; уже созданные файлы останутся.", "sc.token": "Токен бота", "sc.chat": "ID чата (Chat ID)",
      "sc.proxy": "Прокси для Telegram", "sc.proxyPh": "http://… или socks5h://… (пусто = без прокси)", "sc.interval": "Интервал (часы)", "sc.node": "Включить PG-Node",
      "sc.tokenKeep": "Без изменений ({hint})", "sc.saveRestart": "Сохранить и перезапустить", "sc.test": "Тестовое сообщение", "sc.logs": "Живые логи", "sc.notInstalled": "Служба не установлена",
      "sv.title": "Серверы и перенос", "sv.sub": "Перенос PasarGuard на новый сервер в один клик", "sv.add": "Добавить сервер", "sv.name": "Название", "sv.address": "Адрес (IP / домен)",
      "sv.login": "Вход", "sv.sshPort": "Порт SSH", "sv.user": "Пользователь", "sv.pass": "Пароль", "sv.key": "или приватный ключ SSH", "sv.hostfp": "Отпечаток ключа хоста (рекомендуется; ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub)", "sv.note": "Данные для входа хранятся в зашифрованном виде и не возвращаются в браузер.",
      "sv.authKey": "Ключ SSH", "sv.authPw": "Пароль", "sv.transfer": "Перенести сюда", "sv.none": "Серверы не добавлены", "sv.transferTitle": "Перенос на {n}",
      "sv.transferWarn": "Будет создана свежая копия этого сервера и восстановлена на целевом. Данные PasarGuard на целевом сервере будут заменены.",
      "sv.confirmHost": "Для подтверждения введите адрес целевого сервера:", "sv.go": "Начать перенос",
      "jb.title": "Задачи и логи", "jb.sub": "История копирования, восстановления, переноса и конвертации", "jb.none": "Задач ещё не было", "jb.job": "Задача №{n}", "jb.cancel": "Отменить задачу",
      "se.title": "Безопасность", "se.sub": "Пароль, двухфакторная аутентификация и журнал событий", "se.pw": "Смена пароля", "se.pwOld": "Текущий пароль",
      "se.pwNew": "Новый пароль (мин. 10 символов, разный регистр и цифра)", "se.pwBtn": "Сменить пароль", "se.pwDone": "Пароль изменён; остальные сеансы завершены",
      "se.tfa": "Двухфакторная аутентификация (TOTP)", "se.tfaDesc": "Работает с Google Authenticator, Aegis, 2FAS и любым TOTP-приложением.", "se.enable": "Включить", "se.on": "Включена",
      "se.recLeft": "Осталось резервных кодов: {n}", "se.disable": "Отключить", "se.curPw": "Пароль", "se.curCode": "Текущий код аутентификатора", "se.disabled": "Отключено",
      "se.scan": "Отсканируйте QR-код или введите ключ вручную:", "se.code6": "6-значный код", "se.verify": "Подтвердить и включить", "se.recTitle": "Резервные коды",
      "se.recWarn": "Эти коды показываются один раз. Каждый действует однократно. Храните их в надёжном месте.", "se.recOk": "Я сохранил(а)",
      "se.audit": "Журнал событий", "se.time": "Время", "se.user": "Пользователь", "se.ip": "IP", "se.event": "Событие", "se.details": "Подробности"
    },
    fa: {
      _name: "فارسی", _locale: "fa-IR", _dir: "rtl",
      "nav.dashboard": "داشبورد", "nav.backups": "بکاپ‌ها", "nav.schedulers": "زمان‌بندی و تلگرام", "nav.servers": "سرورها و انتقال",
      "nav.jobs": "کارها و لاگ", "nav.security": "امنیت",
      "nav.convert": "تبدیل دیتابیس", "cv.title": "تبدیل نوع دیتابیس", "cv.sub": "از بکاپ یک نسخه برای نوع دیگری از دیتابیس بسازید", "cv.safe": "خروجی یک فایل بکاپ جدید است. هیچ‌چیز ریستور نمی‌شود و این سرور تغییری نمی‌کند.", "cv.live": "بکاپ همین سرور", "cv.liveHint": "از همین سرور بکاپ تازه می‌گیرد (فقط خواندن)، تبدیل می‌کند و فقط فایل تبدیل‌شده را نگه می‌دارد.",
      "sc.skipTest": "پیام آزمایشی تلگرام ارسال نشود (حتی اگر تلگرام از این سرور در دسترس نیست ذخیره شود)",
      "cv.target": "تبدیل به", "cv.liveBtn": "بکاپ و تبدیل", "cv.files": "بکاپ‌های داخل پوشه", "cv.engine": "نوع فعلی", "cv.none": "تبدیل ممکن نیست", "cv.pairs": "پشتیبانی: TimescaleDB ⇄ PostgreSQL و MariaDB ⇄ MySQL. فایل تبدیل‌شده را دانلود کنید، به سرور جدید ببرید و آنجا ریستور کنید.",
      "c.logout": "خروج", "c.cancel": "انصراف", "c.close": "بستن", "c.save": "ذخیره", "c.delete": "حذف", "c.start": "شروع", "c.stop": "توقف",
      "c.restart": "ری‌استارت", "c.back": "قبلی", "c.next": "بعدی", "c.done": "انجام شد", "c.saved": "ذخیره شد", "c.emptyLog": "(خالی)", "c.menu": "منو",
      "c.theme": "پوسته", "c.language": "زبان", "th.system": "سیستم", "th.light": "روشن", "th.dark": "تیره",
      "st.done": "موفق", "st.failed": "ناموفق", "st.running": "در حال اجرا", "st.active": "فعال", "st.stopped": "متوقف", "st.none": "نصب نشده",
      "login.title": "ورود", "login.sub": "مدیریت بکاپ‌های پاسارگارد", "login.user": "نام کاربری", "login.pass": "رمز عبور",
      "login.otp": "کد دو مرحله‌ای (یا کد بازیابی)", "login.otpPh": "123456 یا کد بازیابی", "login.submit": "ورود",
      "login.tooMany": "تلاش‌های زیاد. {s} ثانیه دیگر دوباره امتحان کنید.",
      "dash.title": "داشبورد", "dash.sub": "وضعیت لحظه‌ای سرور و بکاپ‌ها", "dash.load": "بار سیستم", "dash.uptime": "روشن‌بودن سرور: {v}",
      "dash.mem": "حافظه", "dash.disk": "دیسک (محل بکاپ)", "dash.free": "{v} آزاد", "dash.backups": "بکاپ‌ها", "dash.latest": "آخرین: {v}",
      "dash.sched": "زمان‌بندها", "dash.jobs": "کارهای اخیر", "dash.noSched": "زمان‌بندی‌ای تعریف نشده", "dash.noJobs": "کاری اجرا نشده", "dash.everyH": "هر {n} ساعت",
      "u.days": "{n} روز", "u.hours": "{n} ساعت", "u.minutes": "{n} دقیقه",
      "bk.title": "بکاپ‌ها", "bk.sub": "ساخت، دانلود، ریستور و ارسال بکاپ پاسارگارد", "bk.new": "بکاپ جدید", "bk.node": "شامل PG-Node هم باشد",
      "bk.begin": "شروع", "bk.upload": "آپلود بکاپ", "bk.uploaded": "آپلود شد", "bk.file": "فایل", "bk.size": "حجم", "bk.date": "تاریخ",
      "bk.download": "دانلود", "bk.restore": "ریستور", "bk.telegram": "تلگرام", "bk.delTitle": "حذف بکاپ", "bk.delBody": "{n} برای همیشه حذف شود؟",
      "bk.restoreTitle": "ریستور بکاپ", "bk.restoreWarn": "اطلاعات فعلی پاسارگارد (دیتابیس و فایل‌ها) با محتوای این بکاپ جایگزین می‌شود و سرویس‌ها موقتاً متوقف می‌شوند.",
      "bk.restoreType": "برای تأیید نام فایل را دقیقاً وارد کنید:", "bk.none": "هنوز بکاپی وجود ندارد",
      "sc.title": "زمان‌بندی و تلگرام", "sc.sub": "مدیریت زمان‌بندهای بکاپ خودکار، تنظیمات ربات و لاگ زنده",
      "sc.none": "هنوز زمان‌بندی‌ای ساخته نشده. روی «زمان‌بندی جدید» بزنید تا بکاپ خودکار به تلگرام ارسال شود.", "sc.convert": "تبدیل به سرویس", "sc.legacyNote": "این زمان‌بندی با نسخه‌ی قدیمی ساخته شده (توکن داخل فایل سرویس است). تبدیلش کنید تا توکن امن ذخیره شود و از اینجا مدیریت شود.", "sc.oldNote": "این زمان‌بندی بیرون از systemd (screen/tmux) اجرا می‌شود یا نصب نیست. برای مدیریت از اینجا آن را به سرویس تبدیل کنید.", "sc.new": "زمان‌بندی جدید", "sc.name": "نام (اختیاری)", "sc.create": "ساخت و اجرا", "sc.created": "زمان‌بندی ساخته و اجرا شد", "sc.newHint": "در ⁦@BotFather⁩ یک ربات بسازید، به آن پیام بدهید، سپس توکن و شناسه‌ی چت خود را وارد کنید. ابتدا یک پیام آزمایشی ارسال می‌شود تا اشتباه‌ها زود معلوم شوند.", "sc.removeQ": "زمان‌بندی «{name}» حذف شود؟ بکاپ‌گیری متوقف می‌شود؛ فایل‌های بکاپ قبلی می‌مانند.", "sc.token": "توکن ربات", "sc.chat": "شناسه چت (Chat ID)",
      "sc.proxy": "پروکسی تلگرام", "sc.proxyPh": "http://… یا socks5h://… (خالی = بدون پروکسی)", "sc.interval": "فاصله (ساعت)", "sc.node": "شامل PG-Node",
      "sc.tokenKeep": "بدون تغییر ({hint})", "sc.saveRestart": "ذخیره و ری‌استارت", "sc.test": "تست ارسال تلگرام", "sc.logs": "لاگ زنده", "sc.notInstalled": "سرویس نصب نشده",
      "sv.title": "سرورها و انتقال", "sv.sub": "انتقال کامل پاسارگارد به سرور جدید با یک کلیک", "sv.add": "افزودن سرور", "sv.name": "نام", "sv.address": "آدرس (IP/دامنه)",
      "sv.login": "ورود", "sv.sshPort": "پورت SSH", "sv.user": "کاربر", "sv.pass": "رمز", "sv.key": "یا کلید خصوصی SSH", "sv.hostfp": "اثرانگشت کلید سرور (پیشنهادی؛ ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub)", "sv.note": "اطلاعات ورود رمزنگاری شده ذخیره می‌شود و هرگز به مرورگر برنمی‌گردد.",
      "sv.authKey": "کلید SSH", "sv.authPw": "رمز", "sv.transfer": "انتقال به این سرور", "sv.none": "سروری ثبت نشده", "sv.transferTitle": "انتقال به {n}",
      "sv.transferWarn": "یک بکاپ تازه از این سرور گرفته و روی سرور مقصد ریستور می‌شود. اطلاعات پاسارگارد مقصد جایگزین خواهد شد.",
      "sv.confirmHost": "برای تأیید آدرس مقصد را وارد کنید:", "sv.go": "شروع انتقال",
      "jb.title": "کارها و لاگ", "jb.sub": "تاریخچه عملیات بکاپ، ریستور، انتقال و تبدیل دیتابیس", "jb.none": "هنوز کاری اجرا نشده", "jb.job": "کار #{n}", "jb.cancel": "لغو کار",
      "se.title": "امنیت", "se.sub": "رمز عبور، تأیید دو مرحله‌ای و گزارش رویدادها", "se.pw": "تغییر رمز عبور", "se.pwOld": "رمز فعلی",
      "se.pwNew": "رمز جدید (حداقل ۱۰ نویسه، حروف بزرگ/کوچک و عدد)", "se.pwBtn": "تغییر رمز", "se.pwDone": "رمز تغییر کرد؛ سایر نشست‌ها بسته شدند",
      "se.tfa": "تأیید دو مرحله‌ای (TOTP)", "se.tfaDesc": "با Google Authenticator، Aegis، 2FAS یا هر برنامه TOTP دیگر.", "se.enable": "فعال‌سازی", "se.on": "فعال",
      "se.recLeft": "کدهای بازیابی باقی‌مانده: {n}", "se.disable": "غیرفعال‌سازی", "se.curPw": "رمز عبور", "se.curCode": "کد فعلی برنامه احراز هویت", "se.disabled": "غیرفعال شد",
      "se.scan": "QR را اسکن کنید یا کلید را دستی وارد کنید:", "se.code6": "کد ۶ رقمی", "se.verify": "تأیید و فعال‌سازی", "se.recTitle": "کدهای بازیابی",
      "se.recWarn": "این کدها فقط یک‌بار نمایش داده می‌شوند. هرکدام فقط یک‌بار قابل استفاده است. جای امنی نگه دارید.", "se.recOk": "ذخیره کردم",
      "se.audit": "گزارش رویدادها", "se.time": "زمان", "se.user": "کاربر", "se.ip": "IP", "se.event": "رویداد", "se.details": "جزئیات"
    }
  };
  var LANGS = ["en", "ru", "fa"], cur = "en";
  function saved() { try { var v = localStorage.getItem("pgb_lang"); return LANGS.indexOf(v) >= 0 ? v : null; } catch (e) { return null; } }
  function set(l) {
    if (LANGS.indexOf(l) < 0) l = "en"; cur = l;
    try { localStorage.setItem("pgb_lang", l); } catch (e) { }
    document.documentElement.lang = l; document.documentElement.dir = D[l]._dir;
  }
  function t(key, vars) {
    var s = D[cur][key]; if (s == null) s = D.en[key]; if (s == null) return key;
    if (vars) for (var k in vars) s = s.split("{" + k + "}").join(vars[k]);
    return s;
  }
  var init = saved() || "en"; cur = init; document.documentElement.lang = init; document.documentElement.dir = D[init]._dir;
  root.I18N = { t: t, set: set, get: function () { return cur; }, langs: LANGS, name: function (l) { return D[l]._name; }, locale: function () { return D[cur]._locale; } };
  /* dev check: every language must define the same keys as English */
  root.I18N.missing = function () { var out = []; LANGS.forEach(function (l) { Object.keys(D.en).forEach(function (k) { if (D[l][k] == null) out.push(l + ":" + k); }); }); return out; };
  if (typeof module !== "undefined") module.exports = root.I18N;
})(typeof window !== "undefined" ? window : this);
