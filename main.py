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
    return "Warleaks AI Guard v12 is Active 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# 2. جلب المفاتيح من إعدادات البيئة في Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

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

# سجل لحفظ معرفات الأعضاء (username -> user_id) لتنفيذ الأوامر بالمعرف @username فوراً
username_cache = {}

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
# 3. القاموس المترابط الذكي والشامل
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

def execute_immediate_ban(message, user_mention, reason, target_user_id=None, delete_msg=True):
    uid = target_user_id or message.from_user.id
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
    except Exception as e:
        print(f"Immediate Ban error: {e}", flush=True)

def execute_mute_1h(message, target_user_id, user_mention, reason, delete_msg=True):
    if delete_msg and message:
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
    try:
        bot.restrict_chat_member(
            message.chat.id,
            target_user_id,
            until_date=int(time.time()) + 3600,
            can_send_messages=False
        )
        msg = bot.send_message(
            message.chat.id,
            f"🔇 <b>تقييد مؤقت (كتم لمدة ساعة)</b>\n"
            f"👤 العضو: {user_mention}\n"
            f"📌 <b>السبب:</b> {html.escape(reason)}.",
            parse_mode="HTML"
        )
        delete_later(message.chat.id, msg.message_id, delay=20)
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

def execute_warning(message, user_mention, reason, target_user_id=None, delete_msg=True):
    uid = target_user_id or message.from_user.id
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
        delete_later(message.chat.id, msg.message_id, delay=20)
    except Exception as e:
        print(f"Warning execution error: {e}", flush=True)

# دالة لاستخراج العضو المستهدف سواء بالرد (Reply) أو بكتابة معرفه (@username) أو الـ ID
def resolve_target_user(message):
    # 1. إذا كان الأمر بالرد على رسالة
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        cache_user(u)
        return u.id, get_user_mention(u), message.reply_to_message

    # 2. إذا كان الأمر يحتوي على منشن نصي خاص بتلغرام (text_mention)
    if message.entities:
        for ent in message.entities:
            if ent.type == "text_mention" and ent.user:
                cache_user(ent.user)
                return ent.user.id, get_user_mention(ent.user), None

    # 3. إذا كتب المشرف @username بجانب الأمر
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

    # 4. إذا كتب المشرف رقم الـ ID مباشرة بجانب الأمر
    id_match = re.search(r"\b(\d{6,15})\b", message.text or "")
    if id_match:
        uid = int(id_match.group(1))
        return uid, f'<a href="tg://user?id={uid}">العضو ({uid})</a>', None

    return None, None, None

# معالج أوامر المشرفين (يدعم الرد Reply أو كتابة المعرف @username مباشرة)
def handle_admin_commands(message):
    if not message.text:
        return False

    # توحيد النص العربي للأمر وإزالة الـ /
    raw_cmd_text = message.text.strip().lstrip("/")
    # إزالة اليوزرنيم أو الـ ID من النص لمعرفة نوع الأمر
    cmd_only = re.sub(r"(@[a-zA-Z0-9_]+|\b\d{6,15}\b)", "", raw_cmd_text).strip()
    norm_cmd = normalize_arabic(cmd_only).lower()

    UNMUTE_CMDS = {"الغاء الكتم", "الغاء كتم", "فك الكتم", "فك كتم", "رفع الكتم", "عفو", "سماح", "فك الحظر", "الغاء الحظر", "unmute", "unban", "unwarn"}
    MUTE_CMDS = {"كتم", "تقييد", "اسكات", "mute"}
    BAN_CMDS = {"طرد", "حظر", "بان", "ban"}
    WARN_CMDS = {"تحذير", "انذار", "warn"}

    all_cmds = UNMUTE_CMDS | MUTE_CMDS | BAN_CMDS | WARN_CMDS
    if norm_cmd not in all_cmds:
        return False

    target_uid, target_mention, replied_msg = resolve_target_user(message)

    # حذف رسالة أمر المشرف ليبقى الشات نظيفاً
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

    if not target_uid:
        msg = bot.send_message(
            message.chat.id,
            "⚠️ لم أتمكن من تحديد العضو! يرجى عمل <b>رد (Reply)</b> على رسالته أو كتابة معرفه الصحيح مثل: <code>الغاء الكتم @username</code>",
            parse_mode="HTML"
        )
        delete_later(message.chat.id, msg.message_id, delay=10)
        return True

    if norm_cmd in UNMUTE_CMDS:
        unban_flag = norm_cmd in {"فك الحظر", "الغاء الحظر", "unban", "عفو"}
        execute_unmute_or_pardon(message.chat.id, target_uid, target_mention, unban_too=unban_flag)
    elif norm_cmd in MUTE_CMDS:
        execute_mute_1h(replied_msg or message, target_uid, target_mention, "قرار إداري من مشرف المجموعة", delete_msg=bool(replied_msg))
    elif norm_cmd in BAN_CMDS:
        execute_immediate_ban(replied_msg or message, target_mention, "قرار إداري مباشر من مشرف المجموعة", target_user_id=target_uid, delete_msg=bool(replied_msg))
    elif norm_cmd in WARN_CMDS:
        execute_warning(replied_msg or message, target_mention, "مخالفة تنبيهات إدارة المجموعة", target_user_id=target_uid, delete_msg=bool(replied_msg))

    return True

def process_moderation(message, is_edited=False):
    # حفظ معرف المرسل في الذاكرة المؤقتة دائماً
    if message.from_user:
        cache_user(message.from_user)
    if message.reply_to_message and message.reply_to_message.from_user:
        cache_user(message.reply_to_message.from_user)

    if getattr(message, 'is_automatic_forward', False) or message.sender_chat:
        return

    try:
        member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in ['creator', 'administrator']:
            if not is_edited:
                handle_admin_commands(message)
            return
    except Exception:
        pass

    user_mention = get_user_mention(message.from_user)

    if not is_edited and check_is_flooding(message.chat.id, message.from_user.id):
        execute_mute_1h(message, message.from_user.id, user_mention, "الإغراق المتتالي (إرسال رسائل متكررة بسرعة كبيرة)")
        return

    raw_text = (message.text or message.caption or "").strip()
    norm_text = normalize_arabic(raw_text)

    # --- المرحلة أ: الفلتر المترابط السريع ---
    if message.forward_from_chat or getattr(message, 'external_reply', None):
        execute_warning(message, user_mention, "تحويل أو اقتباس رسائل من قنوات خارجية")
        return

    if has_hidden_links(message) or re.search(DIRECT_LINK_PATTERN, raw_text, re.IGNORECASE):
        execute_warning(message, user_mention, "نشر روابط خارجية في المجموعة")
        return

    if not raw_text:
        return

    promo_reason = check_compound_promo(norm_text, raw_text)
    if promo_reason:
        execute_warning(message, user_mention, promo_reason)
        return

    for pattern in RELIGIOUS_COMPOUND_PATTERNS:
        if re.search(pattern, norm_text, re.IGNORECASE):
            execute_immediate_ban(message, user_mention, "سب الدين أو ازدراء المقدسات الدينية")
            return

    for pattern in SEVERE_PROFANITY_PATTERNS:
        if re.search(pattern, norm_text, re.IGNORECASE):
            execute_warning(message, user_mention, "قذف أعراض أو استخدام ألفاظ فاحشة نابية")
            return

    for pattern in TARGETED_INSULTS_PATTERNS:
        if re.search(pattern, norm_text, re.IGNORECASE):
            execute_warning(message, user_mention, "توجيه شتيمة شخصية مباشرة لعضو في المجموعة")
            return

    for pattern in SECTARIAN_COMPOUND_PATTERNS:
        if re.search(pattern, norm_text, re.IGNORECASE):
            execute_warning(message, user_mention, "إثارة النعرات الطائفية والتحقير المذهبي")
            return

    if len(raw_text) > 1500:
        execute_warning(message, user_mention, "الإغراق النصي (إرسال نص طويل جداً يعطل النقاش)")
        return

    # --- المرحلة ب: الفلتر الذكي (Gemini AI) ---
    clean_words = re.sub(r"[^\w\s]", "", norm_text).strip().split()
    if len(clean_words) < 3 or norm_text in SAFE_SHORT_PHRASES:
        return

    action, ai_reason = analyze_with_ai(raw_text)
    if action == "RELIGIOUS_BAN":
        execute_immediate_ban(message, user_mention, ai_reason or "سب الدين وازدراء الأديان والمقدسات")
    elif action == "WARN":
        execute_warning(message, user_mention, ai_reason or "الإساءة اللفظية أو مخالفة آداب النقاش")

@bot.message_handler(
    func=lambda m: m.chat.type in ['group', 'supergroup'],
    content_types=['text', 'photo', 'video', 'document', 'sticker', 'animation']
)
def handle_new_message(message):
    process_moderation(message, is_edited=False)

@bot.edited_message_handler(
    func=lambda m: m.chat.type in ['group', 'supergroup'],
    content_types=['text', 'photo', 'video', 'document']
)
def handle_edited_message(message):
    process_moderation(message, is_edited=True)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    print("Warleaks AI Guard v12 is running...", flush=True)
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)
