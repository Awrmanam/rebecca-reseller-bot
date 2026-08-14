# ربات مدیریت نمایندگی Rebecca

پروژه‌ای مستقل، سبک و ایمنی‌محور برای فروش و پایش نمایندگی Rebecca از طریق تلگرام. Rebecca مرجع وضعیت زنده است و SQLite فقط هماهنگ‌کننده/کش است؛ هیچ دسترسی مستقیم به دیتابیس Rebecca وجود ندارد.

## معماری و امکانات

- `app/rebecca`: آداپتور HTTP تایپ‌شده و fail-closed، کشف قابلیت و مدل‌های API. ساخت نماینده فقط با نقش `reseller` و مجوزهای حداقلی انجام می‌شود و نقش/سهمیه/سرویس بعد از ساخت دوباره خوانده می‌شود.
- `app/reseller` و `app/users`: محاسبه تمدید، تست یک‌باره، انقضا، مالکیت قطعی و حذف تأخیری با بازخوانی زنده.
- `app/payments`: کارت‌به‌کارت صرفاً با تأیید دستی مالک، وضعیت جداگانه PAID/APPLIED، قفل idempotency و callback امضاشده Plisio.
- `app/database`: مدل‌های فشرده audit، محصول، سفارش، پرداخت، تست، کاربر، کانال، هشدار، lock و تنظیمات. SQLite با WAL، foreign keys و busy timeout.
- `app/bot`: منوهای ساده فارسی مشتری/مالک. `FastAPI` فقط health و callback است و UI وب ندارد.
- کنترل‌های پیش‌فرض: `DRY_RUN=true`، تمام disable/delete خاموش، hold مالک، مالکیت زنده، بررسی نهایی و تأیید نتیجه بعد از mutation.

> endpointهای user ذکرشده در مستند Mirza (`/api/user/...`) در آداپتور محصورند. endpointهای مدرن admin ممکن است میان نسخه‌ها فرق کنند؛ فقط قابلیت صریحاً کشف‌شده استفاده می‌شود. OPTIONS برای create قابل مشاهده است و مسیرهای پارامتری admin/update/disable/usage/list-owner تا تأیید نمونه نصب‌شده **خاموش** می‌مانند. fallback، حدس endpoint، SSH یا تغییر DB وجود ندارد.

## ساخت ربات و تنظیم محیط

1. در BotFather ربات بسازید، token را فقط در `.env` قرار دهید و شناسه عددی مالکان را با ویرگول در `OWNER_IDS` بنویسید.
2. فایل محیط را بسازید:

```bash
cp .env.example .env
$EDITOR .env
```

`REBECCA_BASE_URL` و bearer token محدود را وارد کنید. ابتدا token فقط‌خواندنی پیشنهاد می‌شود. secrets هرگز در منو/health/log نشان داده نمی‌شوند. password قوی با `secrets` تولید و فقط یک‌بار تحویل می‌شود؛ بازیابی، reset است نه نمایش password.

### آزمایش اتصال فقط‌خواندنی و قابلیت‌ها

با `DRY_RUN=true` سرویس را بالا بیاورید و health را ببینید:

```bash
curl -fsS http://127.0.0.1:8080/health
sqlite3 ./data/bot.db 'select detected_at, capabilities from capability_snapshots order by id desc limit 1;'
```

snapshot قابلیت در startup ذخیره می‌شود. نبود هر قابلیت، عملیات را متوقف می‌کند؛ اپراتور باید schema/نسخه نصب‌شده را بررسی کند، نه اینکه endpoint جدید حدس زده شود.

## راه‌اندازی پیشنهادی ایمن

### فاز ۱ — مشاهده
`DRY_RUN=true`، `ALLOW_DISABLE_ACTIONS=false`، `ALLOW_DELETE_ACTIONS=false` و `DESTRUCTIVE_ACTIONS=false`. چند روز تصمیم‌های `WOULD_*` را از «📋 تصمیم‌های اخیر»/audit مرور کنید.

### فاز ۲ — غیرفعال‌سازی
پس از تطبیق خواندن زنده: `DRY_RUN=false` و `ALLOW_DISABLE_ACTIONS=true`؛ حذف همچنان خاموش بماند.

### فاز ۳ — حذف
فقط پس از عملیات پایدار و backup: `DESTRUCTIVE_ACTIONS=true` و `ALLOW_DELETE_ACTIONS=true`. حذف کاربر نیازمند سررسید ۷۲ ساعت، بازخوانی زنده، مالکیت تطبیق‌یافته، ادامه انقضا، نبود renewal/lock/hold و تأیید not-found بعد از DELETE است. 🛡 Hold اتوماسیون را متوقف می‌کند.

## تنظیم عملیاتی از تلگرام

مالک به منوهای نمایندگان، پرداخت‌ها، محصولات، تست، عضویت اجباری، تنظیمات، گزارش و audit دسترسی دارد. محصول شامل نام/slug، نوع LITE/PROMAX/CUSTOM، service ID، مدت، حجم، قیمت Decimal و سقف کاربر است؛ حذف محصول دارای سفارش باید soft-delete شود. کانال‌ها را با chat id و لینک عضویت ثبت و ربات را admin کنید؛ trial/purchase/renewal بدون عضویت fail-closed است ولی پشتیبانی مسدود نمی‌شود.

تنظیمات کارت شامل شماره، دارنده، بانک و دستور پرداخت است. رسید photo/document با file-id تلگرام نگهداری و برای مالک ارسال می‌شود؛ **هیچ auto-confirm وجود ندارد** و double-click تأیید دوباره سهمیه نمی‌دهد.

برای Plisio، `PLISIO_ENABLED=true`، secret و currency را فقط در env تنظیم و callback عمومی HTTPS را به این مسیر بدهید:

```text
https://YOUR_HOST/payments/plisio/callback?json=true
```

callback بدون `verify_hash` معتبر، سفارش/txn/مبلغ ناهماهنگ، یا وضعیت غیر `completed` سرویس را فعال نمی‌کند. پول با `Decimal` پردازش می‌شود. سفارش پرداخت‌شده تا تأیید دقیق Rebecca، PAID می‌ماند؛ retry/reconciliation باید target ذخیره‌شده را مقایسه کند تا quota دوباره افزوده نشود.

## Docker روی Ubuntu

```bash
git clone YOUR_REPOSITORY_URL
cd rebecca-reseller-bot
cp .env.example .env
$EDITOR .env
docker compose up -d --build
docker compose logs -f
```

دیتابیس در volume `bot-data` است و HTTP فقط روی localhost:8080 publish می‌شود؛ برای callback از reverse proxy با TLS استفاده کنید. deployment واقعی بخشی از این repository نیست.

### backup و restore

پیش از backup سرویس را متوقف یا از فرمان backup آنلاین SQLite استفاده کنید:

```bash
docker compose exec bot python -c "import sqlite3; s=sqlite3.connect('/data/bot.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"
docker compose cp bot:/data/backup.db ./backup.db
```

برای restore سرویس را پایین بیاورید، نسخه موجود را جدا نگه دارید، فایل معتبر را به volume برگردانید و سپس سرویس را بالا بیاورید. `.env` را جداگانه و رمزگذاری‌شده backup کنید.

## عیب‌یابی، بازیابی پرداخت و audit

- Rebecca unavailable: هیچ حذف/تمدید موفق ثبت نمی‌شود؛ پرداخت حفظ و retry محدود انجام می‌شود.
- قابلیت false: token/version/schema را بررسی کنید؛ هرگز DB Rebecca را تغییر ندهید.
- callback ناموفق: signature، HTTPS عمومی، order number، txn و مبلغ را بررسی و reconciliation را اجرا کنید.
- سفارش PAID ولی APPLIED نشده: snapshot قبل/target و audit را بررسی سپس «🔄 تلاش مجدد»؛ تعلیق دستی (`SUSPENDED`) فقط با اقدام صریح مالک رفع می‌شود.
- خطای Telegram نتیجه موفق Rebecca را rollback نمی‌کند و جداگانه audit می‌شود.

Audit تغییرناپذیر، actor/action/target/result/error و snapshotهای قبل/بعد را نگه می‌دارد. tokenها، کلید Plisio و passwordها نباید وارد audit شوند.

## توسعه و آزمون

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
ruff check .
pytest -q
python -c "import app.main"
```

CI همین سه check را با Python 3.12 اجرا می‌کند. testها mock هستند و به Rebecca واقعی، پرداخت واقعی یا credential واقعی متصل نمی‌شوند.

## محدودیت‌های فعلی

workflowهای اصلی مشتری، رسید، محصول، تست، اعلان، lifecycle و reconciliation پیاده‌سازی شده‌اند. مدیریت پیشرفته به‌جای داشبورد شلوغ با دستورات مالک انجام می‌شود و فهرست‌ها فعلاً ۲۰ مورد اخیر را نشان می‌دهند. endpointهای Rebecca که OpenAPI نصب‌شده advertise نکند عمداً خاموش می‌مانند. پروژه به Rebecca واقعی متصل یا deploy نشده است.

## دستورات مدیریتی تلگرام

علاوه بر منو، مالک می‌تواند عملیات دقیق را با این دستورات انجام دهد:

```text
/product_add slug|name|price|traffic_gb|days|service_ids|users_limit
/product_edit id name|price_toman|traffic_gb|duration_days|service_ids|users_limit|enabled value
/product_delete id
/channel_add chat_id join_url title
/setting key value
/reseller_find telegram_id|telegram_username|rebecca_username
/reseller_hold id
/reseller_disable id
/reseller_enable id
/user_delete rebecca_admin_username username
```

`/product_delete` همیشه soft-delete است. حذف کاربر پس از فرمان نیز نیازمند دکمه تأیید دوم، بازخوانی زنده، تطبیق مالکیت و روشن بودن هر دو کلید حذف است. تنظیم secretهای Plisio/Rebecca/Bot از تلگرام مجاز نیست.

## اجرای زمان‌بندی‌شده

job دوره‌ای با lock پایدار SQLite و batch محدود، نمایندگان و کاربران متعلق به آنان را از Rebecca همگام می‌کند، هشدارهای زمان/حجم را deduplicate می‌کند، پایان اعتبار والد و کاربران را تشخیص می‌دهد، مهلت حذف و هشدار ۲۴ ساعت را ثبت می‌کند، حذف‌های سررسیدشده را با کنترل‌های نهایی اجرا می‌کند و سفارش‌های PAID/APPLYING و Plisio را reconcile می‌کند. هدف دقیق سفارش پیش از mutation خارجی commit می‌شود تا recovery پس از crash سهمیه را دوباره اضافه نکند.

## بازیابی provisioning و تنظیمات runtime

نام کاربری تصادفی نمایندگی و entitlement هدف، پیش از هر درخواست ساخت Rebecca در SQLite ثبت و commit می‌شود. در restart همان نام دقیق جست‌وجو می‌شود؛ اگر admin از قبل وجود داشته باشد، پس از کنترل نقش، password با عملیات update تأییدشده reset می‌شود و admin دوم ساخته نمی‌شود. password هرگز در SQLite یا log ذخیره نمی‌شود. Trial نیز ابتدا نام و وضعیت `PROVISIONING` را به‌صورت یکتا ثبت می‌کند و در recovery همان رکورد را ادامه می‌دهد.

تنظیمات غیرمحرمانه و قابل‌ویرایش مالک (`user_delete_grace_hours` و آستانه‌های هشدار) از `SettingsService` اعتبارسنجی‌شده خوانده می‌شوند و مقدار دیتابیس بر پیش‌فرض env اولویت دارد. tokenها، کلیدها، `DRY_RUN` و کلیدهای disable/delete فقط محیطی باقی می‌مانند.

مقدار traffic صفر/null که میان نسخه‌های Rebecca می‌تواند نامحدود یا مبهم باشد به‌طور محافظه‌کارانه normalize می‌شود؛ lifecycle آن را exhausted تلقی نمی‌کند. این رفتار fail-closed مانع حذف خودکار در وضعیت تأییدنشده می‌شود.
