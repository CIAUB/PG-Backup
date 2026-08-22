# 🛡️ PG-Backup

<div align="center">

### Professional Backup, Restore & Migration Utility for PasarGuard & PG-Node

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=20&pause=1000&color=8B0000&center=true&vCenter=true&width=500&lines=Sharingan-sharp+Backups;Silent+Migration%2C+Zero+Downtime;All+5+Backends+Auto--Detected;Hardened+%26+Security--Audited" alt="Typing SVG" />

![Version](https://img.shields.io/badge/Version-v4.2.1-8B0000)
![Python](https://img.shields.io/badge/Python-3.8+-8B0000)
![Platform](https://img.shields.io/badge/Platform-Linux-8B0000)
![Docker](https://img.shields.io/badge/Docker-Supported-8B0000)
![SQLite](https://img.shields.io/badge/SQLite-Supported-8B0000)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supported-8B0000)
![TimescaleDB](https://img.shields.io/badge/TimescaleDB-Supported-8B0000)
![MySQL](https://img.shields.io/badge/MySQL-Supported-8B0000)
![MariaDB](https://img.shields.io/badge/MariaDB-Supported-8B0000)
![Multi-DB](https://img.shields.io/badge/Multi--Database-Auto--Detected-8B0000)
![Telegram](https://img.shields.io/badge/Telegram-Automation-8B0000)
![SSH](https://img.shields.io/badge/SSH-Migration-8B0000)
![Security](https://img.shields.io/badge/Security-Hardened-8B0000)
![License](https://img.shields.io/badge/License-MIT-8B0000)

Backup • Restore • Migration • Telegram Automation • All 5 Backends • Docker

</div>

---

<div dir="rtl">

# 🚀 معرفی

**PG-Backup** یک ابزار حرفه‌ای برای تهیه نسخه پشتیبان، ریستور و مهاجرت سرویس‌های **PasarGuard** و **PG-Node** است.

این ابزار با هدف ساده‌سازی مدیریت زیرساخت طراحی شده و امکان انتقال کامل سرویس‌ها بین سرورها، ارسال خودکار بکاپ به تلگرام و بازیابی سریع اطلاعات را فراهم می‌کند.

> 🆕 **v4.2.1** — یک نسخه‌ی کاملاً **سخت‌گیرانه‌شده از نظر امنیتی**. کل مسیرهای backup/restore/scheduler در برابر command injection، path traversal (Zip-Slip)، نشت credential و race condition بازبینی و اصلاح شدند. جزئیات کامل در بخش [🔐 امنیت](#-امنیت-security) پایین‌تر.

---

# ✨ قابلیت‌ها

* 📦 بکاپ از PasarGuard
* 📦 بکاپ کامل از PasarGuard + PG-Node
* 🔄 ریستور کامل از فایل ZIP
* 🚀 انتقال مستقیم به سرور جدید
* 🤖 ارسال خودکار بکاپ به تلگرام (با تقسیم خودکار فایل‌های حجیم)
* ⏰ بکاپ زمان‌بندی‌شده (چند شِدیولر هم‌زمان)
* 🗄️ بکاپ و ریستور **همه‌ی دیتابیس‌های پاسارگارد** (Multi-Database)
* 🐳 مدیریت خودکار Docker Stack
* ⚙️ نصب خودکار وابستگی‌ها
* 🔐 انتقال امن از طریق SSH
* 🎯 **تشخیص خودکار بک‌اند** دیتابیس (هیچ سؤالی از کاربر پرسیده نمی‌شود)
* 🛡️ **سخت‌گیری امنیتی کامل**: بدون command injection، بدون Zip-Slip، بدون credential leak

---

# 🗄️ بک‌اندهای دیتابیس پشتیبانی‌شده

ابزار به‌صورت **خودکار** نوع دیتابیس را از فایل `/opt/pasarguard/.env` و `docker-compose.yml` تشخیص می‌دهد — بدون هیچ سؤال یا تنظیم اضافی:

| بک‌اند | ابزار بکاپ | ابزار ریستور |
| --- | --- | --- |
| **SQLite** | کپی فایل `db.sqlite3` | کپی فایل با مسیر مقصد validate‌شده (با توقف موقت پنل) |
| **PostgreSQL** | `pg_dump` + `pg_dumpall` (همه‌ی دیتابیس‌ها) | `psql` (per-database) |
| **TimescaleDB** | `pg_dump` + `pg_dumpall` (با ثبت نسخه‌ی extension) | `psql` (per-database) |
| **MySQL** | `mysqldump` (رمز از طریق `docker compose exec -e`) | `mysql` (با fallback خودکار credential) |
| **MariaDB** | `mysqldump` (رمز از طریق `docker compose exec -e`) | `mysql` (با fallback خودکار credential) |

### فرآیند تشخیص (اتوماتیک)

```text
خواندن /opt/pasarguard/.env
     │
     ▼
پارس SQLALCHEMY_DATABASE_URL
     │
     ▼
اسکن docker-compose.yml
(تشخیص timescaledb از postgres
 و mariadb از mysql با دیدن image)
     │
     ▼
Validate کردن نام سرویس Docker
(فقط [A-Za-z0-9_.-]+ پذیرفته می‌شود)
     │
     ▼
انتخاب ابزار و کانتینر مناسب
     │
     ▼
بکاپ / ریستور خودکار
```

---

# 🛠 نصب

برای نصب PG-Backup کافی است دستور زیر را با دسترسی Root اجرا کنید:

```bash
sudo bash -c "$(curl -sL https://raw.githubusercontent.com/CIAUB/PG-Backup/main/install.sh)"
```

در صورتی که لینک اصلی در دسترس نبود:

```bash
sudo bash -c "$(curl -sL https://raw.githack.com/CIAUB/PG-Backup/main/install.sh)"
```

> 💡 لینک دوم از CDN جایگزین استفاده می‌کند و در برخی شبکه‌ها پایداری بیشتری دارد.

---

# 🚀 اجرا

پس از نصب، برای باز کردن منوی ابزار کافی است دستور زیر را اجرا کنید:

```bash
PG-Backup
```

---

# 🖥️ منوی اصلی

```text
1 ─ 🚀 Auto Backup & Transfer to New Server
2 ─ 🤖 Auto Backup to Telegram Bot (Scheduled)
3 ─ 💾 Manual Backup (Save Locally)
4 ─ 🔄 Manual Restore (From Local ZIP)
5 ─ 🧭 Manage Backup Schedulers (Start / Stop / Restart / Update Token)
6 ─ ⬆️ Update to Latest Version
7 ─ 🚪 Exit
```

---

# 📦 حالت‌های بکاپ

### 1️⃣ PasarGuard Only

```text
/opt/pasarguard
/var/lib/pasarguard
All Pasarguard Databases (Multi-DB)
  ├── globals.sql  (PG/TS only)
  ├── db-001.sql
  ├── db-002.sql
  └── manifest.tsv
```

### 2️⃣ PasarGuard + PG-Node

```text
/opt/pasarguard
/var/lib/pasarguard
All Pasarguard Databases (Multi-DB)

/opt/pg-node
/var/lib/pg-node
```

> 💾 حین ساخته‌شدن بکاپ، محتویات موقت (شامل `.env` و دامپ خام دیتابیس) در یک دایرکتوری موقت **خصوصی (0700)** ساخته می‌شود، نه در `/tmp` عمومی. آرشیو نهایی `.zip` هم با دسترسی `0600` روی دیسک نوشته می‌شود.

---

# 🚀 انتقال به سرور جدید

این قابلیت برای مهاجرت کامل سرویس به سرور جدید طراحی شده است.

### فرآیند انتقال

```text
Detect Backend Auto
     │
     ▼
Create Multi-DB Backup (0700 tmp dir)
     │
     ▼
Send To Telegram (Optional)
     │
     ▼
Connect To New Server (رمز با getpass، بدون echo)
     │
     ▼
Upload Backup
     │
     ▼
Validate Archive Locally (Zip-Slip check)
     │
     ▼
Detect Target Backend Auto
     │
     ▼
Restore Data (Per-Backend)
     │
     ▼
Start Services
```

### اطلاعات موردنیاز

```text
Server IP
Root Password  (فقط با getpass گرفته می‌شود، هرگز echo/ذخیره نمی‌شود)
Telegram Bot Token (Optional)
Telegram Chat ID (Optional)
```

> ⚠️ **درباره‌ی SSH Host Key**: برای اولین اتصال به سرور جدید، کلید میزبان به‌صورت خودکار پذیرفته می‌شود (`AutoAddPolicy`) و یک هشدار صریح قبل از اتصال نمایش داده می‌شود. این یعنی روی شبکه‌های نامطمئن (Wi-Fi عمومی و…) امکان نظری حمله‌ی MITM در همان اولین اتصال وجود دارد. توصیه می‌شود این ابزار را فقط از شبکه‌های قابل‌اعتماد (VPN داخلی، دیتاسنتر) اجرا کنید.

---

# 🤖 بکاپ خودکار تلگرام

امکان تهیه نسخه پشتیبان در بازه زمانی دلخواه و ارسال مستقیم به تلگرام.

نمونه بازه‌ها:

```text
30 Minutes
1 Hour
6 Hours
12 Hours
24 Hours
```

### 📤 آپلود فایل‌های حجیم (>50MB)

Telegram Bot API محدودیت ۵۰ مگابایتی روی هر فایل دارد. اگر آرشیو بکاپ از این حد بزرگ‌تر باشد:

* به‌صورت خودکار به قطعات شماره‌دار (`.001`, `.002`, ...) تقسیم می‌شود
* هر قطعه با کپشن راهنمای join مجدد ارسال می‌شود
* در **Manual Restore**، قطعات موجود در دایرکتوری به‌صورت خودکار شناسایی، از نظر کامل‌بودن verify و بازچسبانده می‌شوند — بدون نیاز به `cat` دستی

### ماندگاری بعد از بستن SSH

هنگام تنظیم بکاپ زمان‌بندی‌شده، ابزار می‌پرسد که شِدیولر بعد از بسته‌شدن سشن SSH چطور زنده بماند:

```text
1 ─ None      (فقط تا وقتی همین ترمینال بازه)
2 ─ screen    (سشن screen جدا در پس‌زمینه)
3 ─ tmux      (سشن tmux جدا در پس‌زمینه)
4 ─ systemd   (سرویس پس‌زمینه، حتی بعد از ری‌بوت هم زنده می‌مونه)
```

برای گزینه‌های ۲ تا ۴ یک **نام نمونه (Instance Name)** پرسیده می‌شود (مثلاً `pasarguard-backup-1`)، تا بشود چند شِدیولر را هم‌زمان و بدون قاطی‌شدن با هم اجرا کرد. نام به‌سختی اعتبارسنجی می‌شود (فقط حروف/عدد/`_`/`-`) تا از تزریق دستور یا path traversal جلوگیری شود.

> 🔐 **توکن بات و Chat ID دیگر روی خط فرمان (CLI args) پاس داده نمی‌شوند** — چون این‌ها از طریق `ps`، `/proc/<pid>/cmdline` و فایل‌های systemd unit (که پیش‌فرض world-readable هستند) قابل خواندن بودند. از v4.2 به بعد، این اطلاعات در یک فایل **`0600`** در `/etc/pasarguard-backup/<instance>.json` ذخیره می‌شوند و پروسه‌ی daemon فقط با پرچم `--instance` آن‌ها را می‌خواند. شِدیولرهای ساخته‌شده با نسخه‌های قدیمی‌تر (که توکن را داخل unit file نگه می‌داشتند) به‌صورت خودکار به فرمت جدید مهاجرت می‌کنند.

---

# 🧭 مدیریت شِدیولرها

از منوی اصلی (گزینه ۵) می‌توان همه‌ی شِدیولرهای فعال یا نصب‌شده (systemd / screen / tmux) را مشاهده و مدیریت کرد:

```text
[systemd] pasarguard-backup-1   RUNNING   (sleeping — 50m until next backup)
[systemd] pasarguard-backup-2   RUNNING   (backing up now)
[screen ] pasarguard_backup-3   RUNNING
```

برای هر شِدیولر می‌توان:

* 🔁 **Restart** — کد جدید را بدون حذف/بازسازی نمونه اعمال می‌کند (systemd صرفاً `restart`، screen/tmux با بازسازی session از روی credential ذخیره‌شده)
* ⏹️ **Stop**
* 🗑️ **Remove** — حذف کامل، شامل unit file و فایل credential
* ✏️ **Update Bot Token / Admin Chat ID** — بدون نیاز به حذف و ساخت دوباره‌ی کل نمونه

---

# 💾 بکاپ دستی

نمونه فایل خروجی:

```text
backup_pg_20260801120000.zip
```

یا:

```text
backup_full_20260801120000.zip
```

---

# 🔄 ریستور دستی

ریستور کامل از فایل ZIP شامل:

* فایل‌های برنامه
* تنظیمات
* **تمام دیتابیس‌های پاسارگارد** (نه فقط یکی)
* داده‌های PG-Node
* داده‌های PasarGuard

### مراحل ریستور

```text
Validate Filename (بدون path/shell metachar)
Stop Containers
Extract Archive (با بررسی Zip-Slip روی تک‌تک entryها)
Restore Files
Detect Backend From .env
Restore Databases (Per-Backend, با اعتبارسنجی مسیر مقصد و نام فایل manifest)
Start Containers
Health Check
```

---

# 📁 ساختار فایل بکاپ

```text
backup_full_YYYYMMDDHHMMSS.zip
│
├── docker-compose.yml
├── .env
│
├── db_dump
│   ├── globals.sql          (فقط برای PostgreSQL / TimescaleDB)
│   ├── db-001.sql           (یا db.sqlite3 برای SQLite)
│   ├── db-002.sql           (PG/TS multi-DB)
│   ├── db-003.sql           (PG/TS multi-DB)
│   └── manifest.tsv
│       # pg_backup_manifest  v4.2  format=tsv  db_type=timescaledb
│       pasarguard  pasarguard  1  db-001.sql  2.17.2
│       analytics   pasarguard  0  db-002.sql
│       node_panel  pasarguard  0  db-003.sql
│
├── pasarguard_data
│
├── pg_node_opt
│
└── pg_node_data
```

---

# 📂 مسیرهای پیش‌فرض

| سرویس      | مسیر کانفیگ       | مسیر داده             |
| ---------- | ----------------- | --------------------- |
| PasarGuard | `/opt/pasarguard` | `/var/lib/pasarguard` |
| PG-Node    | `/opt/pg-node`    | `/var/lib/pg-node`    |

---

# ⬆️ آپدیت به آخرین نسخه

از منوی اصلی (گزینه ۶) می‌توان مستقیم از داخل ابزار به آخرین نسخه آپدیت کرد. برخلاف نصب اولیه، این فرآیند دیگر یک `curl | sudo bash` خام نیست:

```text
Download install.sh   →   دایرکتوری موقت خصوصی (0700)
     │
     ▼
Download install.sh.sha256  (در صورت موجود بودن)
     │
     ▼
مقایسه SHA256  →  در صورت عدم تطابق: توقف کامل
     │
     ▼
اجرای فایل ذخیره‌شده با sudo bash
```

اگر فایل هش در دسترس نباشد، ابزار صراحتاً هشدار می‌دهد و تأیید دستی کاربر را برای ادامه‌ی بدون verification می‌خواهد.

> 💡 شِدیولرهایی که از قبل در حال اجرا هستند (screen/tmux/systemd) کد نسخه‌ی قبلی را در حافظه دارند؛ بعد از آپدیت، از منوی «مدیریت شِدیولرها» آن‌ها را Restart کنید تا نسخه‌ی جدید فعال شود.

---

# 🔐 امنیت (Security)

نسخه‌ی v4.2.1 حاصل یک ممیزی کامل امنیتی روی کل مسیرهای اجرای shell، فایل‌سیستم و شبکه است. مهم‌ترین اصلاحات:

| حوزه | مشکل قبلی | راه‌حل |
| --- | --- | --- |
| **Manual Restore** | نام فایل ZIP بدون escape داخل دستور `shell=True` قرار می‌گرفت (Command Injection) | اعتبارسنجی سخت‌گیرانه‌ی نام فایل (`[A-Za-z0-9_.-]+`) قبل از هر استفاده |
| **استخراج آرشیو** | `unzip -o` به entryهایی مثل `../../etc/cron.d/evil` اجازه‌ی نوشتن می‌داد (Zip-Slip) | استخراج با ماژول `zipfile` پایتون + بررسی صریح مسیر هر entry و رد symlinkهای مشکوک |
| **ریستور SQLite** | مسیر مقصد از `SQLALCHEMY_DATABASE_URL` داخل بکاپ خوانده می‌شد؛ یک بکاپ مخرب می‌توانست فایل دلخواهی را overwrite کند | مسیر مقصد با `realpath` باید حتماً زیرمجموعه‌ی `/var/lib/pasarguard/` باشد |
| **manifest.tsv** | نام فایل SQL داخل manifest بدون بررسی traversal استفاده می‌شد | رد هر مقداری که شامل `..`، `/` یا `\` باشد |
| **توکن/چت‌آیدی تلگرام** | به‌صورت plaintext در CLI args و systemd unit فایل‌ها (world-readable) قرار می‌گرفت | ذخیره در فایل `0600` جدا؛ daemon فقط با `--instance` می‌خواند |
| **رمز MySQL/MariaDB** | `MYSQL_PWD` روی پروسه‌ی هاست ست می‌شد ولی توسط `docker compose exec` به کانتینر forward نمی‌شد | ارسال با `docker compose exec -e MYSQL_PWD=...` مستقیم به کانتینر |
| **دایرکتوری موقت بکاپ** | `/tmp/<name>` با دسترسی جهانی‌خواندنی، حاوی `.env` و دامپ خام دیتابیس | `tempfile.mkdtemp()` با دسترسی `0700` |
| **آرشیو نهایی** | ساخته می‌شد و بعداً `chmod` می‌شد (پنجره‌ی race) | `umask 0077` قبل از ساخت آرشیو + chmod 600 بلافاصله |
| **آپدیت خودکار** | `curl | sudo bash` — payload آلوده مستقیم وارد یک shell روت می‌شد | دانلود → تأیید SHA256 → اجرا از فایل ذخیره‌شده |
| **نام Instance/سرویس Docker** | مسیر فایل، نام unit systemd، نام session screen/tmux و دستورات shell از ورودی کاربر یا `docker-compose.yml` ساخته می‌شدند بدون validation | اعتبارسنجی سخت‌گیرانه (regex) + `shlex.quote()` در همه‌ی نقاط `shell=True` |
| **وضعیت شِدیولرها** | فایل‌های state در `/tmp` نگهداری می‌شدند | انتقال به `/etc/pasarguard-backup/state/` با دسترسی `0700` |

### ⚠️ نکته‌ی باز (به عهده‌ی کاربر)

اتصال SSH در «Auto Backup & Transfer to New Server» از `AutoAddPolicy` استفاده می‌کند — یعنی کلید میزبان سرور جدید در اولین اتصال بدون تأیید پذیرفته می‌شود (با نمایش هشدار صریح). این ریسک تئوریک MITM را روی شبکه‌های نامطمئن باز نگه می‌دارد. توصیه: این ابزار را فقط از شبکه‌های قابل‌اعتماد اجرا کنید، یا کلید میزبان را از قبل به‌صورت دستی verify کنید.

---

# ⚠️ نکات مهم

* اجرای ابزار نیازمند دسترسی Root است.
* هنگام ریستور سرویس‌ها به‌صورت موقت متوقف می‌شوند.
* اطلاعات ورود SSH ذخیره نمی‌شوند و رمز عبور با `getpass` (بدون echo) گرفته می‌شود.
* فایل‌های بکاپ شامل اطلاعات حساس هستند و با دسترسی `0600` ذخیره می‌شوند — همچنان توصیه می‌شود در محل امن نگهداری شوند.
* **رمزهای عبور (DB_PASSWORD و غیره) در خروجی ابزار به‌صورت `****XXXX` نمایش داده می‌شوند.**
* توکن بات تلگرام و Chat ID در فایل‌های `0600` جدا از دستورات اجرا نگه‌داری می‌شوند، نه در CLI/unit fileهای world-readable.

---

<p align="center">
<img src="https://raw.githubusercontent.com/CIAUB/CIAUB/main/sharingan.jpg" width="500" alt="Sharingan" />
</p>

---

# 📞 ارتباط با توسعه‌دهنده

* 👨‍💻 Telegram: https://t.me/CIAUB

* 🐙 GitHub: https://github.com/CIAUB

---

### ❤️ حمایت از پروژه

اگر این پروژه برای شما مفید بوده است، با ثبت ⭐ در GitHub از توسعه آن حمایت کنید.

---

<sub><sub>Developed by CIAUB</sub></sub>

</div>
