import os
import re
import json
import time
import html
import threading
from flask import Flask
import telebot
from google import genai
from google.genai import types

# 1. سيرفر الويب لإبقاء Render مستيقظاً 24/7
app = Flask(__name__)

@app.route('/')
def home():
    return "Warleaks AI Guard v13 (Admin Logs & Custom Mute) is Active 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# 2. جلب المفاتيح من إعدادات البيئة في Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# يمكنك وضع LOG_CHAT_ID في Render، أو ببساطة كتابة "تفعيل السجل" داخل مجموعة المشرفين!
LOG_CHAT_ID = os.environ.get("LOG_CHAT_ID", "").strip()

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

MODELS_TO_TRY = [
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash"
]

# سجلات الذاكرة الذكية
user_warnings = {}
WARNING_EXPIRY_SECONDS = 86400  # 24 ساعة

user_message_timestamps = {}
FLOOD_LIMIT = 5
FLOOD_WINDOW = 6

username_cache = {}
active_log_chat_id = int(LOG_CHAT_ID) if LOG_CHAT_ID.lstrip("-").isdigit() else None

def cache_user(user):
    if user and user.username:
        username_cache[user.username.lower()] = user.id

def normalize_arabic(text):
    if not text:
        return ""
    text = re.sub(r"[\u064B-\u065F\u0640]", "", text)
    text = re.sub(r"[أإآ]", "ا", text)
    text = re.sub(r"ة", "ه", text)
    text = re.sub(r"(.)\1{2,}", r"\1", text)
    return text

# =====================================================================
# 3. دالة إرسال التقارير الدائمة إلى مجموعة المشرفين (لا تُحذف أبداً)
# =====================================================================
def send_admin_log(action_title, source_chat, target_uid, target_mention, reason, offending_text="", by_who="النظام الآلي (AI/Regex)"):
    global active_log_chat_id
    if not active_log_chat_id:
        return

    chat_title = html.escape(source_chat.title or "المجموعة")
    safe_reason = html.escape(reason)
    safe_text = html.escape(offending_text[:700]) if offending_text else "<i>(رسالة وسائط أو أمر مباشر بدون نص)</i>"
    timestamp_str = time.strftime('%Y-%m-%d | %H:%M:%S', time.gmtime(time.time() + 3600))

    log_msg = (
        f"📋 <b>تقرير أمني — {action_title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏠 <b>المجموعة:</b> {chat_title}\n"
        f"👤 <b>العضو:</b> {target_mention}\n"
        f"🆔 <b>المعرف الرقمي:</b> <code>{target_uid}</code>\n"
        f"👮 <b>بواسطة:</b> {html.escape(by_who)}\n"
        f"📌 <b>السبب:</b> {safe_reason}\n"
        f"🕒 <b>التوقيت:</b> {timestamp_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💬 <b>محتوى الرسالة المحذوفة:</b>\n"
        f"<pre>{safe_text}</pre>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>للعفو السريع انسخ وأرسل في المجموعة:</b>\n"
        f"<code>الغاء الكتم {target_uid}</code> أو <code>فك الحظر {target_uid}</code>"
    )
    try:
        bot.send_message(active_log_chat_id, log_msg, parse_mode="HTML")
    except Exception as e:
        print(f"Log send error: {e}", flush=True)

# =====================================================================
# 4. القاموس المترابط الذكي والشامل
# =====================================================================

RELIGIOUS_COMPOUND_PATTERNS = [
    r"\b(يلعن|ينعل|نعل|العن|سب|تسب|يسب|كسم|كس\s+ام|نيك)\s+(دين|رب|اله|رسول|نبي|قران|اسلام|مصحف)(ك|كم|هم|ه|ها|و|نا)?\b",
    r"\b(دينك|ربك|الهك|رسولك|قرانك)\s+(الكلب|الوسخ|النجس|الزباله|القذر|ابن)\b",
    r"\b(يا\s+كافر\s+يا\s+ابن|الاسلام\s+دين\s+ارهاب)\b"
]

SEVERE_PROFANITY_PATTERNS = [
    r"\b(كسمك|كسمكم|كسمه|كسمها|كسمهم|كس\s+ام|كس\s+اخت|طيزك|على\s+زبي|يا\s+زبي|مص\s+زبي)\b",
    r"\b(منيوك|منيك|شرموط|شرموطه|قحبه|عرص|عرصا|معرص|قواد|ديوث|مخنث|عاهر|عاهره)\b",
    r"\b(ابن|ولد|يا\s+ابن|يا\s+ولد|امك|اختك)\s+(القحبه|الشرموطه|العاهره|الزنا|الحرام|الكلبه|المنيوكه|القواد)\b",
    r"\b(انيك|نيك|بنيك)\s+(امك|اختك|عرضك|شرفك|اهلك|حريمك|صاحبتك)\b"
]

TARGETED_INSULTS_PATTERNS = [
    r"\b(يا|انت|انتي|انتم|اشباه|يا\s+ايها)\s+(حمار|غبي|كلب|حيوان|بهيم|بهيمه|بغل|سافل|نذل|قذر|واطي|زباله|حثاله|تافه|احمق|معتوه|مختل|مستحمر|منحط|رخيص|جبان|خسيس|مهجن|لقيط)\b",
    r"\b(حمار|غبي|كلب|حيوان|تافه|سافل|واطي|قذر|زباله)\s+(مثل|زي)\s+(وجهك|شكلك|امثالك|راسك)\b",
    r"\b(انقلع|اخرس|سد\s+فمك|كل\s+زق|كل\s+خرا|كل\s+تبن|بول\s+البعير)\b"
]

SECTARIAN_COMPOUND_PATTERNS = [
    r"\b(كس\s+ام|كسم|يلعن|ينعل|كلاب|انجاس|اقذر|احفاد|اولاد)\s+(الشيعه|السنه|الروافض|الرافضه|النواصب|المجوس|العلويين|الدروز|المسيحيين|الاكراد)\b",
    r"\b(روافض|رافضي|نواصب|ناصبي|مجوسي|صفوي)\s+(نجس|انجاس|كلب|كلاب|خونه|كفار|مشركين|اولاد\s+متعه|عيال\s+متعه)\b",
    r"\b(الشيعه|السنه|العلويين|الدروز|المسيحيين|الاكراد)\s+(كلهم\s+كفار|انجاس|كلاب|خونه|دمهم\s+حلال|يستاهلون\s+الذبح|الى\s+جهنم)\b"
]

PROMO_TOPICS = r"(تداول|التداول|فوركس|بيتكوين|كريبتو|عملات\s+رقميه|استثمار|يستثمر|دخل\s+يومي|دخل\s+اضافي|ربح\s+يومي|ارباح\s+مضمونه|تكبير\s+الحسابات|توصيات|نسخ\s+صفقات|قناتنا|قناتي)"
PROMO_ACTIONS = r"(يراسلني|راسلني|راسلوني|يتواصل|تواصل\s+مع|تواصلوا|خاص|الخاص|انضموا|اشتركوا|تابعوا|مجاني|مضمون|يعطيه\s+الطريقه|اعطيك\s+الطريقه|من\s+جواله|من\s+هاتفك|بدون\s+خبره)"
DIRECT_PROMO_PHRASES = r"(اللي\s+حاب\s+يتعلم\s+التداول|تعلم\s+التداول\s+مجاني|يستثمر\s+بنفسه\s+وفي\s+جواله|ويكون\s+معه\s+دخل\s+يومي|يتواصل\s+معه\s+يعطيه\s+الطريقه|انضموا\s+لقناتي|اشتركوا\s+بقناتي|راسلوني\s+خاص\s+للعمل)"

DIRECT_LINK_PATTERN = r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+|\b[a-zA-Z0-9-]+\.(com|net|org|io|me|co|ly|tv)\b)"

SAFE_SHORT_PHRASES = {
    "الله اكبر", "ما شاء الله", "ماشاء الله", "الحمد لله", "سبحان الله",
    "لا حول ولا قوه الا بالله", "يا رب", "يارب", "اللهم سلم", "عاجل", "تم", "مشكور",
    "احسنت", "صح", "صحيح", "نعم", "لا", "اكيد", "ان شاء الله",
    "حفظهم الله", "رحمه الله", "الله يرحمه", "امين", "اللهم امين"
}

def check_compound_promo(norm_text, raw_text):
    if re.search(DIRECT_PROMO_PHRASES, norm_text):
        return "نشر إعلانات تداول أو استثمار ترويجي"

    if re.search(PROMO_TOPICS, norm_text) and re.search(PROMO_ACTIONS, norm_text):
        return "الإعلان عن تداول/استثمار والدعوة للتواصل أو القنوات"

    mentions = re.findall(r"@([a-zA-Z0-9_]{4,32})", raw_text)
    if mentions:
        if re.search(PROMO_TOPICS, norm_text) or re.search(PROMO_ACTIONS, norm_text):
            return "الترويج لحسابات خارجية ودعوة الأعضاء لمراسلتها"

        for username in mentions:
            if username.lower().endswith("bot"):
                return f"الترويج لبوت خارجي (@{username})"
            try:
                chat_info = bot.get_chat(f"@{username}")
                if chat_info.type in ['channel', 'supergroup', 'group']:
                    return f"الإشارة إلى قناة أو مجموعة خارجية (@{username})"
            except Exception:
                pass

    return None

def has_hidden_links(message):
    entities = (message.entities or []) + (message.caption_entities or [])
    for ent in entities:
        if ent.type in ['url', 'text_link']:
            return True
    return False

def check_is_flooding(chat_id, user_id):
    now = time.time()
    key = (chat_id, user_id)
    timestamps = user_message_timestamps.get(key, [])
    timestamps = [t for t in timestamps if now - t < FLOOD_WINDOW]
    timestamps.append(now)
    user_message_timestamps[key] = timestamps

    if len(timestamps) > FLOOD_LIMIT:
        user_message_timestamps[key] = []
        return True
    return False

SYSTEM_INSTRUCTION = """
أنت مشرف أمني ذكي في مجموعة نقاشات عسكرية وسياسية ساخنة حول أحداث الشرق الأوسط.
القاعدة الذهبية لمنع الظلم:
- لا تحكم أبداً على كلمة مفردة معزولة عن سياقها!
- النقاش السياسي الحاد والسخرية السياسية وانتقاد الجيوش والحكومات مسموح ("ALLOW").

متى تتدخل فقط؟
1. "RELIGIOUS_BAN" (حظر فوري نهائي):
- يستخدم حصراً إذا كان سياق الجملة يسب الله أو الذات الإلهية، يسب الدين، يسب الأنبياء، أو يستهزئ بالقرآن والمقدسات الدينية.

2. "WARN" (حذف الرسالة + إعطاء تحذير مع ذكر السبب الدقيق):
- إذا كانت الجملة ككل عبارة عن إعلان ترويجي (دعوة لتعلم التداول، استثمار، دخل يومي، أو دعوة لمراسلة حساب أو متابعة قناة).
- إذا كانت الجملة توجه شتيمة شخصية قذرة أو قذف أعراض لعضو آخر أو لطائفة كاملة.

3. "ALLOW" (سماح):
- جميع النقاشات السياسية والعسكرية والاقتصادية والردود التفاعلية بين الأعضاء.

أجب بصيغة JSON فقط:
{"action": "ALLOW" | "WARN" | "RELIGIOUS_BAN", "reason": "اكتب سبب المخالفة باختصار ووضوح باللغة العربية"}
"""

def analyze_with_ai(text):
    for model_name in MODELS_TO_TRY:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=text,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    temperature=0.0,
                )
            )
            result = json.loads(response.text)
            action = result.get("action", "ALLOW")
            reason = result.get("reason", "مخالفة قوانين النقاش في المجموعة")
            return action, reason
        except Exception as e:
            print(f"Model {model_name} failed: {e}", flush=True)
            continue
    return "ALLOW", ""

def delete_later(chat_id, message_id, delay=20):
    def task():
        time.sleep(delay)
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
    threading.Thread(target=task, daemon=True).start()

def get_user_mention(user):
    if user.username:
        return f"@{html.escape(user.username)}"
    name = html.escape(user.first_name or "عضو")
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def format_duration_ar(seconds):
    hours = seconds / 3600
    if hours < 1:
        mins = max(1, int(seconds / 60))
        return f"{mins} دقيقة"
    elif hours == 1:
        return "ساعة واحدة"
    elif hours == 2:
        return "ساعتين"
    elif 3 <= hours <= 10 and hours.is_integer():
        return f"{int(hours)} ساعات"
    elif hours.is_integer():
        return f"{int(hours)} ساعة"
    else:
        return f"{hours:.1f} ساعة"

# =====================================================================
# 5. دوال تنفيذ العقوبات مع إرسال إشعار لغرفة المشرفين
# =====================================================================

def execute_immediate_ban(message, user_mention, reason, target_user_id=None, delete_msg=True, by_who="النظام الآلي"):
    uid = target_user_id or message.from_user.id
    offending_text = (message.text or message.caption or "") if message else ""
    if delete_msg and message:
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
    try:
        bot.ban_chat_member(message.chat.id, uid)
        msg = bot.send_message(
            message.chat.id,
            f"🚫 <b>حظر فوري نهائي</b>\n"
            f"👤 العضو: {user_mention}\n"
            f"📌 <b>سبب الحظر:</b> {html.escape(reason)}.",
            parse_mode="HTML"
        )
        delete_later(message.chat.id, msg.message_id, delay=25)
        # إرسال تقرير دائم لمجموعة المشرفين
        send_admin_log("حظر نهائي 🚫", message.chat, uid, user_mention, reason, offending_text, by_who)
    except Exception as e:
        print(f"Immediate Ban error: {e}", flush=True)

def execute_mute(message, target_user_id, user_mention, reason, duration_seconds=3600, delete_msg=True, by_who="النظام الآلي"):
    offending_text = (message.text or message.caption or "") if (delete_msg and message) else ""
    if delete_msg and message:
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass

    # تلغرام يشترط ألا تقل مدة الكتم المؤقت عن 30 ثانية وألا تزيد عن 366 يوماً
    duration_seconds = max(60, min(int(duration_seconds), 365 * 86400))
    duration_str = format_duration_ar(duration_seconds)

    try:
        bot.restrict_chat_member(
            message.chat.id,
            target_user_id,
            until_date=int(time.time()) + duration_seconds,
            can_send_messages=False
        )
        msg = bot.send_message(
            message.chat.id,
            f"🔇 <b>تقييد مؤقت (كتم لمدة {duration_str})</b>\n"
            f"👤 العضو: {user_mention}\n"
            f"📌 <b>السبب:</b> {html.escape(reason)}.",
            parse_mode="HTML"
        )
        delete_later(message.chat.id, msg.message_id, delay=20)
        # إرسال تقرير دائم لمجموعة المشرفين
        send_admin_log(f"كتم لمدة {duration_str} 🔇", message.chat, target_user_id, user_mention, reason, offending_text, by_who)
    except Exception as e:
        print(f"Mute error: {e}", flush=True)

def execute_unmute_or_pardon(chat_id, target_user_id, user_mention, unban_too=False):
    user_warnings.pop((chat_id, target_user_id), None)
    try:
        if unban_too:
            bot.unban_chat_member(chat_id, target_user_id, only_if_banned=True)
        bot.restrict_chat_member(
            chat_id,
            target_user_id,
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
    except Exception as e:
        print(f"Unmute/Unban error: {e}", flush=True)

    msg = bot.send_message(
        chat_id,
        f"✅ تم إلغاء الكتم/الحظر وتصفير التحذيرات للعضو {user_mention}.",
        parse_mode="HTML"
    )
    delete_later(chat_id, msg.message_id, delay=12)

def execute_warning(message, user_mention, reason, target_user_id=None, delete_msg=True, by_who="النظام الآلي"):
    uid = target_user_id or message.from_user.id
    offending_text = (message.text or message.caption or "") if (delete_msg and message) else ""
    if delete_msg and message:
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass

    try:
        now = time.time()
        key = (message.chat.id, uid)
        valid_warnings = [t for t in user_warnings.get(key, []) if now - t < WARNING_EXPIRY_SECONDS]
        valid_warnings.append(now)
        user_warnings[key] = valid_warnings
        warnings_count = len(valid_warnings)

        if warnings_count >= 3:
            bot.ban_chat_member(message.chat.id, uid)
            user_warnings.pop(key, None)
            msg = bot.send_message(
                message.chat.id,
                f"🚫 <b>طرد وحظر نهائي (استنفاد التحذيرات 3/3)</b>\n"
                f"👤 العضو: {user_mention}\n"
                f"📌 <b>سبب المخالفة الأخيرة:</b> {html.escape(reason)}.",
                parse_mode="HTML"
            )
            send_admin_log("حظر نهائي (تراكم 3/3 تحذيرات) 🚫", message.chat, uid, user_mention, reason, offending_text, by_who)
        else:
            remaining = 3 - warnings_count
            msg = bot.send_message(
                message.chat.id,
                f"⚠️ <b>تنبيه وتحذير ({warnings_count}/3)</b>\n"
                f"👤 العضو: {user_mention}\n"
                f"📌 <b>لماذا تم تحذيرك وحذف رسالتك؟</b>\n"
                f"• السبب: <b>{html.escape(reason)}</b>.\n"
                f"⏳ متبقي لك ({remaining}) تحذير قبل الحظر التلقائي من المجموعة.",
                parse_mode="HTML"
            )
            send_admin_log(f"تحذير ({warnings_count}/3) ⚠️", message.chat, uid, user_mention, reason, offending_text, by_who)
        delete_later(message.chat.id, msg.message_id, delay=20)
    except Exception as e:
        print(f"Warning execution error: {e}", flush=True)

# =====================================================================
# 6. استخراج العضو المستهدف ومدة الكتم المخصصة التي يطلبها المشرف
# =====================================================================
def resolve_target_user(message):
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        cache_user(u)
        return u.id, get_user_mention(u), message.reply_to_message

    if message.entities:
        for ent in message.entities:
            if ent.type == "text_mention" and ent.user:
                cache_user(ent.user)
                return ent.user.id, get_user_mention(ent.user), None

    match = re.search(r"@([a-zA-Z0-9_]{3,32})", message.text or "")
    if match:
        uname = match.group(1).lower()
        uid = username_cache.get(uname)
        if not uid:
            try:
                chat_obj = bot.get_chat(f"@{uname}")
                uid = chat_obj.id
            except Exception:
                pass
        if uid:
            return uid, f"@{html.escape(match.group(1))}", None

    id_match = re.search(r"\b(\d{6,15})\b", message.text or "")
    if id_match:
        uid = int(id_match.group(1))
        return uid, f'<a href="tg://user?id={uid}">العضو ({uid})</a>', None

    return None, None, None

def extract_mute_duration_seconds(cmd_text):
    # حذف اليوزرنيم والـ ID الطويل من النص حتى لا يختلط رقم الـ ID بعدد الساعات
    cleaned = re.sub(r"(@[a-zA-Z0-9_]+|\b\d{6,15}\b)", "", cmd_text)
    # البحث عن رقم (عدد الساعات أو الدقائق أو الأيام) من 1 إلى 4 خانات
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*(د|دقيقه|دقيقة|دقائق|س|ساعه|ساعة|ساعات|ي|يوم|ايام|أيام|m|h|d)?\b", cleaned, re.IGNORECASE)
    if not m:
        return 3600  # الافتراضي: ساعة واحدة (3600 ثانية)

    val = float(m.group(1))
    unit = (m.group(2) or "س").lower()
    if unit.startswith(("د", "m")):
        return int(val * 60)
    elif unit.startswith(("ي", "ا", "أ", "d")):
        return int(val * 86400)
    else:
        return int(val * 3600)  # الساعات هي الافتراضي لأي رقم يكتبه المشرف

def handle_admin_commands(message):
    global active_log_chat_id
    if not message.text:
        return False

    raw_cmd_text = message.text.strip().lstrip("/")
    norm_full = normalize_arabic(raw_cmd_text).lower()

    # أمر تفعيل مجموعة السجل للمشرفين
    if norm_full in {"تفعيل السجل", "ربط السجل", "setlog", "سجل المشرفين"}:
        active_log_chat_id = message.chat.id
        bot.send_message(
            message.chat.id,
            f"✅ <b>تم اعتماد هذه المجموعة كغرفة سجل ومراقبة للمشرفين!</b>\n"
            f"🆔 معرف المجموعة (لحفظه في Render داخل خانة <code>LOG_CHAT_ID</code> إن أردت ثباته الدائم): <code>{message.chat.id}</code>\n"
            f"📌 ستصلك هنا جميع تقارير الحظر والكتم والتحذيرات مع نصوص الرسائل المحذوفة دون أن تُمسح.",
            parse_mode="HTML"
        )
        return True

    # تنظيف النص لاستخراج نوع الأمر الأساسي (مع إزالة اليوزرنيم، الـ ID، وأرقام الساعات)
    cmd_only = re.sub(r"(@[a-zA-Z0-9_]+|\b\d+(?:\.\d+)?\s*(?:د|دقيقه|دقيقة|دقائق|س|ساعه|ساعة|ساعات|ي|يوم|ايام|أيام|m|h|d)?\b)", "", raw_cmd_text).strip()
    norm_cmd = normalize_arabic(cmd_only).lower()

    UNMUTE_CMDS = {"الغاء الكتم", "الغاء كتم", "فك الكتم", "فك كتم", "رفع الكتم", "عفو", "سماح", "فك الحظر", "الغاء الحظر", "unmute", "unban", "unwarn"}
    MUTE_CMDS = {"كتم", "تقييد", "اسكات", "mute"}
    BAN_CMDS = {"طرد", "حظر", "بان", "ban"}
    WARN_CMDS = {"تحذير", "انذار", "warn"}

    all_cmds = UNMUTE_CMDS | MUTE_CMDS | BAN_CMDS | WARN_CMDS
    if norm_cmd not in all_cmds:
        return False

    target_uid, target_mention, replied_msg = resolve_target_user(message)
    admin_name = f"المشرف ({message.from_user.first_name})"

    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

    if not target_uid:
        msg = bot.send_message(
            message.chat.id,
            "⚠️ لم أتمكن من تحديد العضو! يرجى عمل <b>رد (Reply)</b> على رسالته أو كتابة معرفه مثل: <code>كتم 3 @username</code>",
            parse_mode="HTML"
        )
        delete_later(message.chat.id, msg.message_id, delay=10)
        return True

    if norm_cmd in UNMUTE_CMDS:
        unban_flag = norm_cmd in {"فك الحظر", "الغاء الحظر", "unban", "عفو"}
        execute_unmute_or_pardon(message.chat.id, target_uid, target_mention, unban_too=unban_flag)
    elif norm_cmd in MUTE_CMDS:
        duration_sec = extract_mute_duration_seconds(raw_cmd_text)
        exe
