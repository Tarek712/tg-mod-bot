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
    return "Warleaks AI Guard v10 (Smart Contextual Dictionary) is Active 24/7!"

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
WARNING_EXPIRY_SECONDS = 86400  # تسقط التحذيرات بعد 24 ساعة من الالتزام

user_message_timestamps = {}
FLOOD_LIMIT = 5
FLOOD_WINDOW = 6

# دالة توحيد وتنظيف النص العربي (إزالة التطويل "حماااار" -> "حمار" وتوحيد الهمزات)
def normalize_arabic(text):
    if not text:
        return ""
    # إزالة التشكيل والمد (التطويل)
    text = re.sub(r"[\u064B-\u065F\u0640]", "", text)
    # توحيد الهمزات
    text = re.sub(r"[أإآ]", "ا", text)
    text = re.sub(r"ة", "ه", text)
    # تقليص الحروف المكررة أكثر من مرتين (مثلاً: غبيييي -> غبي)
    text = re.sub(r"(.)\1{2,}", r"\1", text)
    return text

# =====================================================================
# 3. القاموس المترابط الذكي (لا يعاقب إلا على تراكيب مترابطة مقصودة 100%)
# =====================================================================

# أ) سب الدين والذات الإلهية (تركيب مترابط: فعل سب/لعن + لفظ مقدس) -> حظر فوري نهائي!
RELIGIOUS_COMPOUND_PATTERNS = [
    # لعن/سب + دين/رب/إله/رسول/اسلام/قرآن (بمختلف اللهجات الشامية والمغاربية والمصرية والخليجية)
    r"\b(يلعن|ينعل|نعل|العن|سب|تسب|يسب|كسم|كس ام|نيك)\s+(دين|رب|اله|رسول|نبي|قران|اسلام|مصحف)(ك|كم|هم|ه|ها|و|نا)?\b",
    r"\b(دينك|ربك|الهك|رسولك|قرانك)\s+(الكلب|الوسخ|النجس|الزباله|القذر|ابن)\b",
    r"\b(يا\s+كافر\s+يا\s+ابن|الاسلام\s+دين\s+ارهاب|دين\s+محمد\s+ال)\b"
]

# ب) الفحش الجنسي الصريح وقذف الأعراض والأمهات -> تحذير فوري (وفي الـ 3 حظر)
SEVERE_PROFANITY_PATTERNS = [
    r"\b(كسمك|كسمكم|كسمه|كسمها|كس\s+امك|كس\s+اختك|طيزك|على\s+زبي|يا\s+زبي|مص\s+زبي)\b",
    r"\b(ابن|ولد|يا\s+ابن|يا\s+ولد|امك|اختك)\s+(القحبه|الشرموطه|العاهره|الزنا|الحرام|الكلبه|المنيوكه|القواد)\b",
    r"\b(يا|انت|انتم)\s+(قحبه|شرموط|شرموطه|منيوك|مخنث|قواد|ديوث|لوطي|عاهر|عاهره)\b",
    r"\b(انيك|نيك|بنيك)\s+(امك|اختك|عرضك|شرفك|اهلك|حريمك|صاحبتك)\b"
]

# ج) الشتائم الشخصية الموجهة (بشرط وجود أداة توجيه "يا / أنت / يالـ" + الشتيمة، حتى لا يُحظر من يصف خبر أو عدو)
TARGETED_INSULTS_PATTERNS = [
    r"\b(يا|انت|انتي|انتم|اشباه|يـا|يا\s+ايها)\s+(حمار|غبي|كلب|حيوان|بهيم|بهيمه|بغل|سافل|نذل|قذر|واطي|زباله|حثاله|تافه|احمق|معتوه|مختل|مستحمر|منحط|رخيص|جبان|خسيس|مهجن|لقيط)\b",
    r"\b(حمار|غبي|كلب|حيوان|تافه|سافل|واطي|قذر|زباله)\s+(مثل|زي)\s+(وجهك|شكلك|امثالك|راسـك)\b",
    r"\b(انقلع|اخرس|سد\s+فمك|كرول|غور|كل\s+زق|كل\s+خرا|كل\s+تبن|بول\s+البعير)\b"
]

# د) الطائفية المقترنة بالتحقير أو التكفير أو الشتم (يسمح بذكر الطوائف في الخبر ويمنع الشتم الطائفي)
SECTARIAN_COMPOUND_PATTERNS = [
    r"\b(يا|هؤلاء|ذول|هدول|كلاب|احفاد|ابناء|اولاد|انجس|اقذر)\s+(الروافض|الرافضه|النواصب|المجوس|المرتدين|الدواعش|الخوارج|النصيريه)\b",
    r"\b(روافض|رافضي|نواصب|ناصبي|مجوسي|صفوي)\s+(نجس|انجاس|كلب|كلاب|خونه|كفار|مشركين|اولاد\s+متعه|عيال\s+متعه)\b",
    r"\b(الشيعه|السنه|العلويين|الدروز|المسيحيين|الاكراد)\s+(كلهم\s+كفار|انجاس|كلاب|خونه|دمهم\s+حلال|يستاهلون\s+الذبح|الى\s+جهنم)\b"
]

# هـ) القاموس المترابط للترويج والاحتيال المالي (شرط مزدوج: كلمة مالية/قنوات + فعل دعوة واستقطاب)
PROMO_TOPICS = r"(تداول|التداول|فوركس|بيتكوين|كريبتو|عملات\s+رقميه|استثمار|يستثمر|دخل\s+يومي|دخل\s+اضافي|ربح\s+يومي|ارباح\s+مضمونه|تكبير\s+الحسابات|توصيات|نسخ\s+صفقات|قناتنا|قناتي)"
PROMO_ACTIONS = r"(يراسلني|راسلني|راسلوني|يتواصل|تواصل\s+مع|تواصلوا|خاص|الخاص|انضموا|اشتركوا|تابعوا|مجاني|مضمون|يعطيه\s+الطريقه|اعطيك\s+الطريقه|من\s+جواله|من\s+هاتفك|بدون\s+خبره)"

# عبارات ترويجية صريحة بذاتها
DIRECT_PROMO_PHRASES = r"(اللي\s+حاب\s+يتعلم\s+التداول|تعلم\s+التداول\s+مجاني|يستثمر\s+بنفسه\s+وفي\s+جواله|ويكون\s+معه\s+دخل\s+يومي|يتواصل\s+معه\s+يعطيه\s+الطريقه|انضموا\s+لقناتي|اشتركوا\s+بقناتي|راسلوني\s+خاص\s+للعمل)"

# ب) الروابط الصريحة
DIRECT_LINK_PATTERN = r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+|\b[a-zA-Z0-9-]+\.(com|net|org|io|me|co|ly|tv)\b)"

# و) القائمة البيضاء للعبارات الإخبارية والدينية القصيرة
SAFE_SHORT_PHRASES = {
    "الله اكبر", "ما شاء الله", "ماشاء الله", "الحمد لله", "سبحان الله",
    "لا حول ولا قوه الا بالله", "يا رب", "يارب", "اللهم سلم", "عاجل", "تم", "مشكور",
    "احسنت", "صح", "صحيح", "نعم", "لا", "اكيد", "ان شاء الله",
    "حفظهم الله", "رحمه الله", "الله يرحمه", "امين", "اللهم امين"
}

# 4. دالة فحص الترويج المركّب (تمنع رسائل التداول والاستثمار الوهمي دون منع النقاش الاقتصادي العادي)
def check_compound_promo(norm_text, raw_text):
    # 1. إذا احتوت على عبارة ترويجية كاملة مطابقة
    if re.search(DIRECT_PROMO_PHRASES, norm_text):
        return "نشر إعلانات تداول أو استثمار ترويجي"

    # 2. إذا اجتمع في الرسالة (موضوع مالي/تداول/قناة) + (فعل استقطاب ومراسلة) معاً!
    if re.search(PROMO_TOPICS, norm_text) and re.search(PROMO_ACTIONS, norm_text):
        return "الإعلان عن تداول/استثمار والدعوة للتواصل أو القنوات"

    # 3. فحص المنشن (@username)
    mentions = re.findall(r"@([a-zA-Z0-9_]{4,32})", raw_text)
    if mentions:
        # إذا وجد منشن ومعه أي كلمة استقطاب أو مال في نفس الرسالة
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

# 5. تعليمات الذكاء الاصطناعي (يفحص السياق الكامل بدقة)
SYSTEM_INSTRUCTION = """
أنت مشرف أمني ذكي في مجموعة نقاشات عسكرية وسياسية ساخنة حول أحداث الشرق الأوسط.
القاعدة الذهبية لمنع الظلم:
- لا تحكم أبداً على كلمة مفردة معزولة عن سياقها!
- إذا استخدم العضو كلمات قاسية لوصف حدث عسكري أو سياسي أو عدو خارج المجموعة (مثل: "قصف عنيف"، "هذا جيش غبي"، "خطة فاشلة"، "التداول بالبورصة هبط") فهذا نقاش طبيعي مسموح تماماً ("ALLOW").
- النقاش السياسي الحاد والسخرية السياسية بين الأعضاء مسموحة طالما لم تتحول إلى بذاءة أو قذف.

متى تتدخل فقط؟
1. "RELIGIOUS_BAN" (حظر فوري نهائي):
- يستخدم حصراً إذا كان سياق الجملة يسب الله أو الذات الإلهية، يسب الدين، يسب الأنبياء، أو يستهزئ بالقرآن والمقدسات الدينية.

2. "WARN" (حذف الرسالة + إعطاء تحذير مع ذكر السبب الدقيق):
- إذا كانت الجملة ككل عبارة عن إعلان ترويجي (دعوة لتعلم التداول، استثمار، دخل يومي، أو دعوة لمراسلة حساب أو متابعة قناة).
- إذا كانت الجملة توجه شتيمة شخصية قذرة أو قذف أعراض لعضو آخر داخل النقاش.
- إذا كانت الجملة تحرض طائفياً أو تكفر طائفة كاملة بألفاظ مسيئة.

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

def execute_immediate_ban(message, user_mention, reason):
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        print(f"Delete error: {e}", flush=True)
    try:
        bot.ban_chat_member(message.chat.id, message.from_user.id)
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

def execute_mute_1h(message, target_user, reason):
    user_mention = get_user_mention(target_user)
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass
    try:
        bot.restrict_chat_member(
            message.chat.id,
            target_user.id,
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

def execute_warning(message, user_mention, reason, target_user_id=None):
    uid = target_user_id or message.from_user.id
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        print(f"Delete msg error: {e}", flush=True)

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

def handle_admin_commands(message):
    if not message.reply_to_message or not message.text:
        return False

    cmd = message.text.strip()
    if cmd not in ["طرد", "حظر", "كتم", "تحذير", "عفو"]:
        return False

    target_msg = message.reply_to_message
    target_user = target_msg.from_user
    if not target_user:
        return True

    target_mention = get_user_mention(target_user)

    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

    if cmd in ["طرد", "حظر"]:
        execute_immediate_ban(target_msg, target_mention, "قرار إداري مباشر من مشرف المجموعة")
    elif cmd == "كتم":
        execute_mute_1h(target_msg, target_user, "قرار إداري من مشرف المجموعة")
    elif cmd == "تحذير":
        execute_warning(target_msg, target_mention, "مخالفة تنبيهات إدارة المجموعة", target_user_id=target_user.id)
    elif cmd == "عفو":
        user_warnings.pop((message.chat.id, target_user.id), None)
        try:
            bot.restrict_chat_member(
                message.chat.id,
                target_user.id,
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        except Exception:
            pass
        msg = bot.send_message(
            message.chat.id,
            f"✅ تم إصدار عفو إداري لـ {target_mention} (تم تصفير التحذيرات ورفع التقييد).",
            parse_mode="HTML"
        )
        delete_later(message.chat.id, msg.message_id, delay=12)

    return True

def process_moderation(message, is_edited=False):
    if getattr(message, 'is_automatic_forward', False) or message.sender_chat:
        return

    # حماية المشرفين وتفعيل أوامرهم
    try:
        member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in ['creator', 'administrator']:
            if not is_edited:
                handle_admin_commands(message)
            return
    except Exception:
        pass

    user_mention = get_user_mention(message.from_user)

    # فحص الإغراق المتتالي
    if not is_edited and check_is_flooding(message.chat.id, message.from_user.id):
        execute_mute_1h(message, message.from_user, "الإغراق المتتالي (إرسال رسائل متكررة بسرعة كبيرة)")
        return

    raw_text = (message.text or message.caption or "").strip()
    norm_text = normalize_arabic(raw_text)

    # --- المرحلة أ: الفلتر المترابط السريع (بدون أخطاء الكلمات المفردة) ---
    # 1. تحويل رسائل أو اقتباس من قنوات خارجية
    if message.forward_from_chat or getattr(message, 'external_reply', None):
        execute_warning(message, user_mention, "تحويل أو اقتباس رسائل من قنوات خارجية")
        return

    # 2. روابط صريحة أو مخفية تحت النص
    if has_hidden_links(message) or re.search(DIRECT_LINK_PATTERN, raw_text, re.IGNORECASE):
        execute_warning(message, user_mention, "نشر روابط خارجية في المجموعة")
        return

    if not raw_text:
        return

    # 3. فحص الترويج المترابط (موضوع تداول/استثمار + دعوة للمراسلة أو القنوات)
    promo_reason = check_compound_promo(norm_text, raw_text)
    if promo_reason:
        execute_warning(message, user_mention, promo_reason)
        return

    # 4. فحص سب الدين المترابط -> حظر فوري بدون تحذيرات!
    for pattern in RELIGIOUS_COMPOUND_PATTERNS:
        if re.search(pattern, norm_text, re.IGNORECASE):
            execute_immediate_ban(message, user_mention, "سب الدين أو ازدراء المقدسات الدينية")
            return

    # 5. فحص الفحش الجنسي الصريح وقذف الأعراض
    for pattern in SEVERE_PROFANITY_PATTERNS:
        if re.search(pattern, norm_text, re.IGNORECASE):
            execute_warning(message, user_mention, "قذف أعراض أو استخدام ألفاظ فاحشة نابية")
            return

    # 6. فحص الشتائم الشخصية الموجهة (يشترط وجود أداة نداء/مخاطبة + الشتيمة)
    for pattern in TARGETED_INSULTS_PATTERNS:
        if re.search(pattern, norm_text, re.IGNORECASE):
            execute_warning(message, user_mention, "توجيه شتيمة شخصية مباشرة لعضو في المجموعة")
            return

    # 7. فحص الطائفية المقترنة بالتحقير أو التكفير
    for pattern in SECTARIAN_COMPOUND_PATTERNS:
        if re.search(pattern, norm_text, re.IGNORECASE):
            execute_warning(message, user_mention, "إثارة النعرات الطائفية والتحقير المذهبي")
            return

    # 8. فحص الإغراق النصي الطويل جداً (أكثر من 1500 حرف)
    if len(raw_text) > 1500:
        execute_warning(message, user_mention, "الإغراق النصي (إرسال نص طويل جداً يعطل النقاش)")
        return

    # --- المرحلة ب: الفلتر الذكي (Gemini AI) لفهم السياق الكامل ---
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
    print("Warleaks AI Guard v10 (Smart Contextual Dictionary) is running...", flush=True)
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)
