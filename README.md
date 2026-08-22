# 🛡️ PG-Backup

<div align="center">

### Professional Backup, Restore & Migration Utility for PasarGuard & PG-Node

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=20&pause=1000&color=8B0000&center=true&vCenter=true&width=500&lines=Sharingan-sharp+Backups;Silent+Migration%2C+Zero+Downtime;All+5+Backends+Auto--Detected;PostgreSQL+%26+Docker+Automation" alt="Typing SVG" />

![Version](https://img.shields.io/badge/Version-v4.1-8B0000)
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
![License](https://img.shields.io/badge/License-MIT-8B0000)

Backup • Restore • Migration • Telegram Automation • All 5 Backends • Docker

</div>

---

<div dir="rtl">

# 🚀 معرفی

**PG-Backup** یک ابزار حرفه‌ای برای تهیه نسخه پشتیبان، ریستور و مهاجرت سرویس‌های **PasarGuard** و **PG-Node** است.

این ابزار با هدف ساده‌سازی مدیریت زیرساخت طراحی شده و امکان انتقال کامل سرویس‌ها بین سرورها، ارسال خودکار بکاپ به تلگرام و بازیابی سریع اطلاعات را فراهم می‌کند.

> 🆕 **v4.1** — تشخیص خودکار **تمام ۵ بک‌اند رسمی پاسارگارد** (SQLite، PostgreSQL، TimescaleDB، MySQL، MariaDB) از روی `SQLALCHEMY_DATABASE_URL` در `.env` و اسکن `docker-compose.yml`. همچنین پشتیبانی کامل از **تمام دیتابیس‌های پاسارگارد** (نه فقط دیتابیس پیش‌فرض).

---

# ✨ قابلیت‌ها

* 📦 بکاپ از PasarGuard
* 📦 بکاپ کامل از PasarGuard + PG-Node
* 🔄 ریستور کامل از فایل ZIP
* 🚀 انتقال مستقیم به سرور جدید
* 🤖 ارسال خودکار بکاپ به تلگرام
* ⏰ بکاپ زمان‌بندی‌شده
* 🗄️ بکاپ و ریستور **همه‌ی دیتابیس‌های پاسارگارد** (Multi-Database)
* 🐳 مدیریت خودکار Docker Stack
* ⚙️ نصب خودکار وابستگی‌ها
* 🔐 انتقال امن از طریق SSH
* 🎯 **تشخیص خودکار بک‌اند** دیتابیس (هیچ سؤالی از کاربر پرسیده نمی‌شود)

---

# 🗄️ بک‌اندهای دیتابیس پشتیبانی‌شده (v4.1)

ابزار به‌صورت **خودکار** نوع دیتابیس را از فایل `/opt/pasarguard/.env` و `docker-compose.yml` تشخیص می‌دهد — بدون هیچ سؤال یا تنظیم اضافی:

| بک‌اند | ابزار بکاپ | ابزار ریستور |
| --- | --- | --- |
| **SQLite** | کپی فایل `db.sqlite3` | کپی فایل (با توقف موقت پنل) |
| **PostgreSQL** | `pg_dump` + `pg_dumpall` (همه‌ی دیتابیس‌ها) | `psql` (per-database) |
| **TimescaleDB** | `pg_dump` + `pg_dumpall` (با ثبت نسخه‌ی extension) | `psql` (per-database) |
| **MySQL** | `mysqldump` | `mysql` (با fallback خودکار credential) |
| **MariaDB** | `mysqldump` | `mysql` (با fallback خودکار credential) |

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
5 ─ 🧭 Manage Backup Schedulers (Start / Stop / Restart)
6 ─ ⬆️ Update to Latest Version
7 ─ 🚪 Exit
```

> 🆕 **v4.1**: تمام workflowهای بکاپ (۱، ۲، ۳) **تمام دیتابیس‌های پاسارگارد** را بکاپ می‌گیرند و تمام workflowهای ریستور (۱، ۴) **خودکار** بک‌اند مقصد را از روی `.env` استخراج‌شده تشخیص می‌دهند.

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

---

### 2️⃣ PasarGuard + PG-Node

```text
/opt/pasarguard
/var/lib/pasarguard
All Pasarguard Databases (Multi-DB)

/opt/pg-node
/var/lib/pg-node
```

---

# 🚀 انتقال به سرور جدید

این قابلیت برای مهاجرت کامل سرویس به سرور جدید طراحی شده است.

### فرآیند انتقال

```text
Detect Backend Auto
     │
     ▼
Create Multi-DB Backup
     │
     ▼
Send To Telegram (Optional)
     │
     ▼
Connect To New Server
     │
     ▼
Upload Backup
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
Root Password
Telegram Bot Token (Optional)
Telegram Chat ID (Optional)
```

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

### ماندگاری بعد از بستن SSH

هنگام تنظیم بکاپ زمان‌بندی‌شده، ابزار می‌پرسد که شِدیولر بعد از بسته‌شدن سشن SSH چطور زنده بماند:

```text
1 ─ None      (فقط تا وقتی همین ترمینال بازه)
2 ─ screen    (سشن screen جدا در پس‌زمینه)
3 ─ tmux      (سشن tmux جدا در پس‌زمینه)
4 ─ systemd   (سرویس پس‌زمینه، حتی بعد از ری‌بوت هم زنده می‌مونه)
```

برای گزینه‌های ۲ تا ۴ یک **نام نمونه (Instance Name)** هم پرسیده می‌شود (مثلاً `pasarguard-backup-1`، `pasarguard-backup-2`)، تا بشود چند شِدیولر را هم‌زمان و بدون قاطی‌شدن با هم اجرا کرد — مثلاً یکی برای هر سرور یا هر بازه‌ی زمانی. اسم پیشنهادی به‌صورت خودکار افزایش پیدا می‌کند، اما می‌شود اسم دلخواه هم گذاشت.

---

# 🧭 مدیریت شِدیولرها

از منوی اصلی (گزینه ۵) می‌توان همه‌ی شِدیولرهای فعال یا نصب‌شده (systemd / screen / tmux) را مشاهده و مدیریت کرد، بدون نیاز به دانستن دستورات `systemctl`/`screen`/`tmux`:

```text
[systemd] pasarguard-backup-1   RUNNING   (sleeping — 50m until next backup)
[systemd] pasarguard-backup-2   RUNNING   (backing up now)
[screen ] pasarguard_backup-3   RUNNING
```

برای هر شِدیولر می‌توان:

* 🔁 **Restart** (فقط systemd)
* ⏹️ **Stop**
* 🗑️ **Remove** (حذف کامل، شامل حذف unit file برای systemd)

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
Stop Containers
Restore Files
Detect Backend From .env
Restore Databases (Per-Backend)
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
│       # pg_backup_manifest  v4.1  format=tsv  db_type=timescaledb
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

از منوی اصلی (گزینه ۶) می‌توان مستقیم از داخل ابزار به آخرین نسخه آپدیت کرد؛ همان دستور نصب دوباره اجرا می‌شود:

```bash
sudo bash -c "$(curl -sL https://raw.githubusercontent.com/CIAUB/PG-Backup/main/install.sh)"
```

> 💡 شِدیولرهایی که از قبل در حال اجرا هستند (screen/tmux/systemd) کد نسخه‌ی قبلی را در حافظه دارند؛ بعد از آپدیت، از منوی «مدیریت شِدیولرها» آن‌ها را Restart کنید تا نسخه‌ی جدید فعال شود.

---

# ⚠️ نکات مهم

* اجرای ابزار نیازمند دسترسی Root است.
* هنگام ریستور سرویس‌ها به‌صورت موقت متوقف می‌شوند.
* اطلاعات ورود SSH ذخیره نمی‌شوند.
* فایل‌های بکاپ شامل اطلاعات حساس هستند.
* توصیه می‌شود نسخه‌های پشتیبان در محل امن نگهداری شوند.
* **رمزهای عبور (DB_PASSWORD و غیره) در خروجی ابزار به‌صورت `****XXXX` نمایش داده می‌شوند.**

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
