# 🛡️ PG-Backup

<div align="center">

### Professional Backup, Restore & Migration Utility for PasarGuard & PG-Node

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=20&pause=1000&color=8B0000&center=true&vCenter=true&width=500&lines=Sharingan-sharp+Backups;Silent+Migration%2C+Zero+Downtime;All+5+Backends+Auto--Detected;Hardened+%26+Security--Audited" alt="Typing SVG" />

![Version](https://img.shields.io/badge/Version-v4.2.8-8B0000)
![Python](https://img.shields.io/badge/Python-3.8+-8B0000)
![Platform](https://img.shields.io/badge/Platform-Linux-8B0000)
![Docker](https://img.shields.io/badge/Docker-Supported-8B0000)
![SQLite](https://img.shields.io/badge/SQLite-Supported-8B0000)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supported-8B0000)
![TimescaleDB](https://img.shields.io/badge/TimescaleDB-Supported-8B0000)
![MySQL](https://img.shields.io/badge/MySQL-Supported-8B0000)
![MariaDB](https://img.shields.io/badge/MariaDB-Supported-8B0000)
![Multi-DB](https://img.shields.io/badge/Multi--Database-Auto--Detected-8B0000)

Backup • Restore • Migration • Telegram Automation • All 5 Backends • Docker

</div>

---

<div dir="rtl" align="center">

[معرفی](#-معرفی) • [قابلیت‌ها](#-قابلیت‌ها) • [بک‌اندها](#️-بک‌اندهای-دیتابیس-پشتیبانی‌شده) • [نصب](#-نصب-و-اجرا) • [انتقال به سرور جدید](#-انتقال-به-سرور-جدید) • [بکاپ تلگرام](#-بکاپ-خودکار-تلگرام) • [تغییرات نسخه](#-تغییرات-نسخه) • [امنیت](#-امنیت-security)

</div>

---

<div dir="rtl">

# 🚀 معرفی

**PG-Backup** یک ابزار حرفه‌ای برای تهیه نسخه پشتیبان، ریستور و مهاجرت سرویس‌های **PasarGuard** و **PG-Node** است — با تشخیص خودکار بک‌اند، انتقال کامل بین سرورها، ارسال خودکار به تلگرام و بازیابی سریع.

> 🆕 **v4.2.8** — یکسان‌سازی کامل احراز هویت PostgreSQL/TimescaleDB بین بکاپ، ریستور و enumeration دیتابیس‌ها: کاربر و رمز واقعی همیشه از `.env` یا (در نبود آن) از بلاک `environment:` سرویس در `docker-compose.yml` خوانده می‌شوند — با اولویت به نام سرویسی که واقعاً تشخیص داده شده، نه یک فهرست حدسیِ ثابت. قبلاً بکاپ Postgres/TimescaleDB بدون رمز و با کاربر هاردکد اجرا می‌شد و در نصب‌های password-enforced یا با کاربر/نام سرویس سفارشی، می‌توانست fail شود یا اشتباه دیتابیس تشخیص دهد. جزئیات در [📜 تغییرات نسخه](#-تغییرات-نسخه) و [🔐 امنیت](#-امنیت-security).

---

# ✨ قابلیت‌ها

* 📦 بکاپ PasarGuard تنها یا PasarGuard + PG-Node
* 🗄️ بکاپ و ریستور **همه‌ی دیتابیس‌های پاسارگارد** (Multi-Database)
* 🎯 **تشخیص خودکار بک‌اند** دیتابیس — بدون سؤال از کاربر
* 🚀 انتقال مستقیم و کامل به سرور جدید (Zero-Downtime Migration)
* 🔄 ریستور کامل از فایل ZIP محلی
* 🤖 ارسال خودکار بکاپ به تلگرام + تقسیم خودکار فایل‌های حجیم (>50MB)
* ⏰ بکاپ زمان‌بندی‌شده با چند شِدیولر هم‌زمان (screen / tmux / systemd)
* 🐳 مدیریت خودکار Docker Stack + نصب خودکار وابستگی‌ها
* 🔐 انتقال امن از طریق SSH با رمز `getpass` (بدون echo)
* 🛡️ سخت‌گیری امنیتی کامل: بدون command injection، بدون Zip-Slip، بدون credential leak
* ✅ **بررسی سلامت آرشیو پیش از هر عملیات مخرب** (v4.2.4) — یک بکاپ ناقص هرگز باعث پاک‌شدن سرور مقصد یا نصب فعلی نمی‌شود
* 🔑 **احراز هویت واقعی Postgres/TimescaleDB در بکاپ و enumeration** (v4.2.5 – v4.2.8) — کاربر/رمز از `.env` یا `docker-compose.yml`، با پشتیبانی از نام سرویس و کاربر سفارشی

---

# 🗄️ بک‌اندهای دیتابیس پشتیبانی‌شده

نوع دیتابیس به‌صورت خودکار از `/opt/pasarguard/.env` و `docker-compose.yml` تشخیص داده می‌شود:

| بک‌اند | بکاپ | ریستور |
| --- | --- | --- |
| **SQLite** | کپی فایل `db.sqlite3` | کپی با مسیر مقصد validate‌شده |
| **PostgreSQL** | `pg_dump` + `pg_dumpall` (همه‌ی DBها، با کاربر/رمز resolve‌شده) | `psql` (per-database، با کاربر/رمز resolve‌شده) |
| **TimescaleDB** | مثل PostgreSQL + ثبت نسخه‌ی extension | `psql` (per-database) |
| **MySQL / MariaDB** | `mysqldump --databases` (خودکفا، شامل `CREATE DATABASE`) | `mysql` با fallback خودکار چند credential |

### فرآیند تشخیص

```text
خواندن .env و پارس SQLALCHEMY_DATABASE_URL
        │
        ▼
اسکن docker-compose.yml (تشخیص timescaledb از postgres، mariadb از mysql)
        │
        ▼
Validate نام سرویس Docker  →  انتخاب ابزار و کانتینر مناسب
        │
        ▼
Resolve کاربر/رمز واقعی: .env  →  (fallback) بلاک environment: سرویس در
docker-compose.yml — با اولویت نام سرویس تشخیص‌داده‌شده (v4.2.5 – v4.2.8)
```

---

# 🛠 نصب و اجرا

```bash
sudo bash -c "$(curl -sL https://raw.githubusercontent.com/CIAUB/PG-Backup/main/install.sh)"
```

لینک جایگزین (در صورت عدم دسترسی):

```bash
sudo bash -c "$(curl -sL https://raw.githack.com/CIAUB/PG-Backup/main/install.sh)"
```

پس از نصب:

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
5 ─ 🧭 Manage Backup Schedulers
6 ─ ⬆️ Update to Latest Version
7 ─ 🚪 Exit
```

---

# 🚀 انتقال به سرور جدید

```text
Detect Backend Auto
        │
        ▼
Resolve Postgres/MySQL Credentials (.env → docker-compose.yml fallback)  (v4.2.5 – v4.2.8)
        │
        ▼
Create Multi-DB Backup (tmp dir خصوصی 0700)
        │
        ▼
✅ Verify Archive Locally — manifest.tsv معتبر است؟ (v4.2.4)
   ✗ نامعتبر → توقف همین‌جا، هیچ سروری دست نمی‌خورد
        │
        ▼
Send To Telegram (اختیاری)
        │
        ▼
Connect To New Server (رمز با getpass)  →  Upload  →  Zip-Slip Validate
        │
        ▼
Detect Target Backend Auto
        │
        ▼
اگر MySQL/MariaDB: تشخیص و پاک‌سازی صحیح data-dir
(bind mount یا named volume) پیش از init مجدد کانتینر (v4.2.3)
        │
        ▼
Restore Data (Per-Backend, با retry روی خواندن manifest ریموت)
        │
        ▼
Start Services
```

### اطلاعات موردنیاز

```text
Server IP
Root Password  (فقط getpass، هرگز echo/ذخیره نمی‌شود)
Telegram Bot Token / Chat ID (اختیاری)
```

> ⚠️ اتصال SSH از `AutoAddPolicy` استفاده می‌کند (پذیرش خودکار کلید میزبان در اولین اتصال، با هشدار صریح). فقط از شبکه‌های قابل‌اعتماد اجرا کنید.

---

# 🤖 بکاپ خودکار تلگرام

بازه‌های پشتیبانی‌شده: `30m` `1h` `6h` `12h` `24h`

* فایل‌های >50MB به‌صورت خودکار به قطعات `.001` `.002` ... تقسیم می‌شوند و در Manual Restore به‌صورت خودکار شناسایی، verify و بازچسبانده می‌شوند.
* ماندگاری شِدیولر بعد از بستن SSH: `None` / `screen` / `tmux` / `systemd`، هرکدام با یک نام نمونه (Instance Name) اعتبارسنجی‌شده.
* توکن بات و Chat ID هرگز روی CLI یا unit فایل نیستند — در فایل `0600` جدا در `/etc/pasarguard-backup/<instance>.json` ذخیره می‌شوند.

از منوی «مدیریت شِدیولرها» (گزینه ۵) می‌توان هر شِدیولر را **Restart** (بدون حذف/بازسازی)، **Stop**، **Remove** یا توکن/Chat ID آن را **Update** کرد.

---

# 📁 ساختار فایل بکاپ

```text
backup_full_YYYYMMDDHHMMSS.zip
│
├── docker-compose.yml
├── .env
│
├── db_dump
│   ├── globals.sql        (فقط PostgreSQL / TimescaleDB)
│   ├── db-001.sql         (یا db.sqlite3 برای SQLite)
│   ├── db-002.sql         (Multi-DB)
│   └── manifest.tsv
│       # pg_backup_manifest  v4.2  format=tsv  db_type=timescaledb
│       pasarguard  pasarguard  1  db-001.sql  2.17.2
│       analytics   pasarguard  0  db-002.sql
│
├── pasarguard_data
├── pg_node_opt
└── pg_node_data
```

---

# 📂 مسیرهای پیش‌فرض

| سرویس | کانفیگ | داده |
| --- | --- | --- |
| PasarGuard | `/opt/pasarguard` | `/var/lib/pasarguard` |
| PG-Node | `/opt/pg-node` | `/var/lib/pg-node` |

---

# ⬆️ آپدیت به آخرین نسخه

از منوی اصلی (گزینه ۶):

```text
Download install.sh + install.sh.sha256   →   tmp dir خصوصی (0700)
        │
        ▼
مقایسه SHA256  →  عدم تطابق: توقف کامل
        │
        ▼
اجرای فایل ذخیره‌شده با sudo bash
```

اگر هش در دسترس نباشد، تأیید دستی کاربر برای ادامه‌ی بدون verification لازم است.

> 💡 بعد از آپدیت، شِدیولرهای در حال اجرا را از «مدیریت شِدیولرها» Restart کنید تا کد جدید فعال شود.

---

# 📜 تغییرات نسخه

آخرین تغییرات مهم:

* **v4.2.8** — پاس دادن `PGPASSWORD` واقعی به `pg_dumpall`/`pg_dump` در بکاپ محلی (نه فقط در ریستور)، همراه با اولویت‌دادن به نام سرویس compose تشخیص‌داده‌شده هنگام fallback به `docker-compose.yml`.
* **v4.2.7** — پاس‌ورد resolve‌شده اکنون در enumeration دیتابیس‌های Postgres هم forward می‌شود؛ رفع سقوط بی‌صدا به حدس تک‌دیتابیسی روی نصب‌های password-enforced.
* **v4.2.6** — رفع تشخیص کاربر سفارشی Postgres در enumeration؛ دیگر با `-U pasarguard` هاردکد fail نمی‌شود.
* **v4.2.5** — کشف credential از بلاک `environment:` در `docker-compose.yml` (علاوه بر `.env`) برای Postgres و MySQL.
* **v4.2.4** — رفع ریشه‌ای «manifest.tsv not found» در مهاجرت: آرشیو بکاپ پیش از هر عملیات مخرب به‌صورت محلی اعتبارسنجی می‌شود.
* **v4.2.3** — رفع `1045 Access denied` در ریستور MySQL/MariaDB با تشخیص و پاک‌سازی صحیح data-dir مقصد.
* **v4.2.2** — رفع خواندن manifest از سرور ریموت + انتظار آماده‌شدن دیتابیس بر اساس نوع بک‌اند.

تاریخچه‌ی کامل هر نسخه (از v4.0 تا امروز) در **[CHANGELOG.md](CHANGELOG.md)**.

---

# 🔐 امنیت (Security)

| حوزه | مشکل قبلی | راه‌حل |
| --- | --- | --- |
| Manual Restore | نام فایل ZIP بدون escape (Command Injection) | اعتبارسنجی سخت‌گیرانه‌ی نام فایل |
| استخراج آرشیو | `unzip -o` به Zip-Slip اجازه می‌داد | استخراج با `zipfile` + بررسی مسیر هر entry |
| ریستور SQLite | مسیر مقصد از بکاپ خوانده می‌شد (overwrite دلخواه) | `realpath` محدود به `/var/lib/pasarguard/` |
| manifest.tsv | نام فایل SQL بدون بررسی traversal | رد هر مقدار شامل `..`, `/`, `\` |
| توکن/چت‌آیدی تلگرام | plaintext در CLI/unit فایل (world-readable) | فایل `0600` جدا؛ خواندن فقط با `--instance` |
| رمز MySQL/MariaDB | `MYSQL_PWD` به کانتینر forward نمی‌شد | `docker compose exec -e MYSQL_PWD=...` |
| دایرکتوری موقت بکاپ | `/tmp` جهانی‌خواندنی | `tempfile.mkdtemp()` با `0700` |
| آرشیو نهایی | chmod بعد از ساخت (race) | `umask 0077` + chmod 600 بلافاصله |
| آپدیت خودکار | `curl \| sudo bash` خام | دانلود → SHA256 → اجرا |
| نام Instance/سرویس | بدون validation در `shell=True` | regex سخت‌گیرانه + `shlex.quote()` |
| MySQL data-dir مقصد | بین دو مهاجرت رمز عوض نمی‌شد → `1045` | تشخیص و پاک‌سازی bind mount/volume پیش از init |
| مهاجرت با manifest ناقص | کشف خطا فقط بعد از پاک‌شدن سرور مقصد | بررسی محلی آرشیو پیش از هر عملیات مخرب |
| بکاپ Postgres/TimescaleDB بدون رمز | فقط `backend["user"]` بدون پاس‌ورد؛ روی نصب password-enforced fail می‌شد | `PGPASSWORD` resolve‌شده از `.env`/`docker-compose.yml` به `pg_dump`/`pg_dumpall` پاس داده می‌شود (v4.2.8) |
| enumeration دیتابیس با کاربر/سرویس سفارشی | کاربر `pasarguard` و نام سرویس‌های حدسی هاردکد بودند | کاربر/رمز واقعی resolve‌شده + اولویت به نام سرویس تشخیص‌داده‌شده (v4.2.5 – v4.2.8) |

> ⚠️ **نکته‌ی باز**: اتصال SSH از `AutoAddPolicy` استفاده می‌کند (پذیرش خودکار کلید میزبان، با هشدار صریح) — ریسک تئوریک MITM روی شبکه‌های نامطمئن. توصیه: فقط از شبکه‌های قابل‌اعتماد اجرا کنید یا کلید میزبان را از قبل دستی verify کنید.

---

# ⚠️ نکات مهم

* اجرا نیازمند دسترسی Root است؛ هنگام ریستور سرویس‌ها موقتاً متوقف می‌شوند.
* اطلاعات SSH ذخیره نمی‌شوند؛ رمز با `getpass` گرفته می‌شود.
* فایل‌های بکاپ حاوی اطلاعات حساس‌اند و با `0600` ذخیره می‌شوند — در محل امن نگه دارید.
* رمزهای عبور در خروجی ابزار به‌صورت `****XXXX` نمایش داده می‌شوند.
* توکن بات و Chat ID همیشه در فایل `0600` جدا هستند، نه در CLI/unit فایل.

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

