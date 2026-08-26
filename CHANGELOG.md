# 📜 Changelog — PG-Backup

<div dir="rtl">

## v4.2.8 — یکسان‌سازی احراز هویت PostgreSQL/TimescaleDB

* `_backup_postgres_local` اکنون credential ها را از طریق `_resolve_pg_credentials()` (اول `.env`، سپس فال‌بک به `docker-compose.yml`) می‌خواند و `PGPASSWORD` را به `pg_dumpall`/`pg_dump` پاس می‌دهد — دقیقاً هماهنگ با چیزی که ریستور قبلاً انجام می‌داد. قبلاً بکاپ فقط از `backend["user"]` بدون هیچ رمزی استفاده می‌کرد، پس در هر نصبی که auth با رمز را اجباری کرده بود، یا `POSTGRES_USER`/`POSTGRES_PASSWORD` فقط در `docker-compose.yml` بود (نه `.env`)، بکاپ می‌توانست fail شود یا کاربر را اشتباه تشخیص دهد؛ حتی وقتی ریستور همان نصب درست کار می‌کرد.
* `_list_databases_local`/`_ssh` و `_pg_db_timescale_info` هم اکنون `PGPASSWORD` را forward می‌کنند تا شمارش دیتابیس‌ها و تشخیص اکستنشن TimescaleDB روی نصب‌های password-enforced بی‌صدا به حدس تک‌دیتابیسِ قدیمی سقوط نکند.
* `_resolve_pg_credentials()` اکنون ابتدا نام سرویس compose که واقعاً تشخیص داده شده را امتحان می‌کند، پیش از افتادن روی فهرست حدسیِ ثابت (`postgres`/`timescaledb`/`db`/`database`) — نصب‌هایی که سرویس Postgres/TimescaleDB در `docker-compose.yml`شان نام دیگری دارد (مثل `postgresql`، `pg`، `timescale` یا یک نام دلخواه) قبلاً credential هایی که همان‌جا موجود بود را بی‌صدا از دست می‌دادند.

## v4.2.7 — پاس رمز در enumeration دیتابیس‌های Postgres

* پاس‌ورد resolve‌شده اکنون به‌صورت `PGPASSWORD` در enumeration دیتابیس‌ها هم forward می‌شود؛ بدون آن، هر سرویس Postgres/TimescaleDB که auth با رمز را اجبار می‌کرد (پیش‌فرض ایمیج‌های رسمی postgres/timescaledb) این فراخوانی `psql` را رد می‌کرد و enumeration حتی با رمز درست، بی‌صدا به حدس تک‌دیتابیسِ قدیمی سقوط می‌کرد.

## v4.2.6 — رفع تشخیص کاربر سفارشی Postgres در enumeration

* `_list_databases_local` اکنون کاربر واقعیِ resolve‌شده را می‌پذیرد به‌جای هاردکد کردن `pasarguard`. در هر نصبی با کاربر سفارشی Postgres (از طریق `SQLALCHEMY_DATABASE_URL` یا `DB_USER` در `.env`)، auth قدیمی با `-U pasarguard` بی‌صدا fail می‌شد، به نام دیتابیس تک و قدیمیِ `pasarguard` سقوط می‌کرد، و `pg_dump` سعی می‌کرد دیتابیسی را دامپ کند که اغلب اصلاً وجود نداشت — دقیقاً برای همان نصب‌هایی که بیشترین نیاز به بکاپ چند-دیتابیسی را دارند.

## v4.2.5 — کشف credential از docker-compose.yml برای Postgres و MySQL

* توابع کمکی مشترک credential-resolution برای Postgres و MySQL اضافه شدند که هر دو قرارداد نام‌گذاری اسکریپت (`DB_USER`/`DB_PASSWORD`، `PG_USER`/`PG_PASSWORD`) و قرارداد ایمیج رسمی (`POSTGRES_USER`/`POSTGRES_PASSWORD`، `MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_ROOT_PASSWORD`) را می‌پذیرند.
* اگر `.env` چیزی نداشته باشد، اکنون خروجی `docker compose config` پارس می‌شود تا بلاک `environment:` سرویس دیتابیس استخراج شود (هم استایل `KEY: value` و هم `- KEY=value`) — برای نصب‌هایی که credential ها مستقیماً در `docker-compose.yml` هاردکد شده‌اند، نه در `.env`.
* این منطق در ریستور MySQL/MariaDB روی سرور ریموت هم به‌کار گرفته شد تا فهرست کاندیدهای user/password کامل‌تر باشد.

## v4.2.4 — رفع ریشه‌ای «manifest.tsv not found» در مهاجرت

* بکاپ تازه‌ساز به‌صورت **محلی** بلافاصله بعد از ساخته‌شدن اعتبارسنجی می‌شود (وجود `db_dump/manifest.tsv` + حداقل یک ردیف دیتا) — هم در Auto Transfer و هم در Manual Restore، **پیش از** توقف کانتینرها یا پاک‌شدن هر دایرکتوری. قبلاً این خطا فقط بعد از آپلود کامل + پاک‌شدن سرور مقصد + ری‌استارت کانتینرها کشف می‌شد.
* خواندن manifest از سرور ریموت اکنون یک بار با تأخیر کوتاه retry می‌شود.
* در صورت نبود واقعی manifest، خروجی تشخیصی شامل `find -maxdepth 2` کل دایرکتوری Pasarguard است تا مشخص شود extract کجا رفته.

## v4.2.3 — رفع `1045 Access denied` در ریستور MySQL/MariaDB

* پیش از start شدن MySQL روی سرور مقصد، data directory آن (bind mount **یا** named volume — با پارس `docker-compose.yml`) تشخیص داده و پاک می‌شود؛ چون MySQL فقط در init اول رمز `MYSQL_ROOT_PASSWORD` را از env می‌خواند و دیتای قدیمی روی bind mount با `docker compose down -v` پاک نمی‌شود.
* `mysqldump` اکنون با `--databases` اجرا می‌شود تا dump خودکفا (شامل `CREATE DATABASE`/`USE`) باشد؛ برای بکاپ‌های قدیمی‌تر بدون این پرچم، این دستورات پیش از restore به‌صورت خودکار prepend می‌شوند.

## v4.2.2 — رفع خواندن manifest از سرور ریموت + آماده‌سازی MySQL

* خواندن `manifest.tsv` در مسیر انتقال به سرور جدید اکنون از طریق SSH روی خود سرور ریموت انجام می‌شود (قبلاً به‌اشتباه فایل محلی خوانده می‌شد و همیشه خالی بود).
* انتظار برای آماده‌شدن دیتابیس (`wait_db`) اکنون بر اساس نوع بک‌اند است (`pg_isready` برای Postgres/TimescaleDB، `mysqladmin ping` برای MySQL/MariaDB) — قبلاً همیشه `pg_isready` روی MySQL هم اجرا می‌شد و timeout می‌داد.
* manifest فقط وقتی نوشته می‌شود که **تمام** دامپ‌ها موفق باشند.

## v4.2.1 — سخت‌گیری امنیتی

* دایرکتوری موقت بکاپ از `/tmp` به `tempfile.mkdtemp()` با دسترسی `0700` منتقل شد.
* آپدیت خودکار: `curl | sudo bash` جایگزین شد با دانلود → تأیید SHA256 → اجرا.
* اعتبارسنجی سخت‌گیرانه‌ی نام Instance/سرویس Docker در همه‌ی مسیرهای `shell=True`.
* مسیر مقصد ریستور SQLite با `realpath` محدود به `/var/lib/pasarguard/` شد.
* نام فایل SQL در manifest در برابر path-traversal (`..`, `/`, `\`) رد می‌شود.

## v4.2 — امنیت و باگ‌های پایه

* رفع command injection در Manual Restore (نام فایل ZIP).
* رفع `MYSQL_PWD` که به کانتینر forward نمی‌شد — اکنون با `docker compose exec -e`.
* توکن بات/Chat ID دیگر در CLI args یا unit فایل نیست — فایل `0600` جدا.
* رمزها با `getpass` (بدون echo)؛ آرشیوهای بکاپ `chmod 600`.
* پشتیبانی از تقسیم/بازچسبانی خودکار فایل‌های تلگرام >50MB.
* Restart درجای شِدیولر بدون حذف/بازسازی + Update توکن درجا.

## v4.1 / v4.0 — پایه

* تشخیص و پشتیبانی کامل هر ۵ بک‌اند پنل رسمی PasarGuard (sqlite, postgresql, timescaledb, mysql, mariadb).
* بکاپ و ریستور **همه‌ی** دیتابیس‌های پاسارگارد، نه فقط `pasarguard`.

</div>
