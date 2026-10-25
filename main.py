import os
import re
import json
import time
import threading
from flask import Flask
import telebot
from google import genai
from google.genai import types

# 1. سيرفر الويب لإبقاء Render مستيقظاً 24/7
app = Flask(__name__)

@app.route('/')
def home():
    return "Warleaks AI Guard v8 (Ultimate Edition) is Active 24/7!"

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

# سجلات الذاكرة الذكية:
# أ) سجل التحذيرات بصلاحية 24 ساعة: {(chat_id, user_id): [timestamp1, timestamp2, ...]}
user_warnings = {}
WARNING_EXPIRY_SECONDS = 86400  # 24 ساعة

# ب) سجل مكافحة الإغراق (Anti-Flood): {(chat_id, user_id): [timestamp1, timestamp2, ...]}
user_message_timestamps = {}
FLOOD_LIMIT = 5       # أقصى عدد رسائل متتالية
FLOOD_WINDOW = 6      # خلال 6 ثوانٍ فقط

# 3. القواميس البرمجية الدقيقة
# أ) سب الدين والذات الإلهية الصريح -> حظر وطرد فوري بدون تحذيرات!
RELIGIOUS_INSULTS_WORDS = [
    r"\bيلعن دينك\b", r"\bينعل دينك\b", r"\bيلعن دين\b", r"\bينعل دين\b",
    r"\bيلعن ربك\b", r"\bينعل ربك\b", r"\bيلعن رب\b", r"\bينعل رب\b",
    r"\bسب الله\b", r"\bسب الدين\b", r"\bسب الرسول\b", r"\bكسم دينك\b"
]

# ب) الروابط الصريحة والدعوات الترويجية المباشرة
DIRECT_LINK_PATTERN = r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+|\b[a-zA-Z0-9-]+\.(com|net|org|io|me|co|ly|tv)\b)"

# ج) عبارات الترويج والاحتيال
PROMO_PHRASES_PATTERN = r"(انضموا لقنا|اشتركوا في قنا|تابعوا قنا|قناتي الاحتياطية|قناتي على التلجرام|راسلوني خاص|تواصل خاص|واتساب|لتكبير الحسابات|توصيات مضمونة|ارباح يومية|ربح مضمون)"

# د) الشتائم الشخصية والألفاظ النابية -> نظام الـ 3 تحذيرات
PERSONAL_INSULTS_WORDS = [
    r"\bابن القحبة\b", r"\bولد القحبة\b", r"\bقحبة\b", r"\bشرموطة\b", r"\bشرموط\b",
    r"\bمنيوك\b", r"\bكسمك\b", r"\bكس امك\b", r"\bكس اختك\b", r"\bنيك امك\b",
    r"\bيا حمار\b", r"\bيا غبي\b", r"\bيا كلب\b", r"\bيا حيوان\b", r"\bيا بهيم\b",
    r"\bيا بغل\b", r"\bيا سافل\b", r"\bيا نذل\b", r"\bيا قذر\b"
]

# هـ) الألفاظ الطائفية الصريحة -> نظام الـ 3 تحذيرات
SECTARIAN_WORDS = [
    r"\bروافض\b", r"\bرافضي\b", r"\bنواصب\b", r"\bناصبي\b"
]

# و) القائمة البيضاء للعبارات الإخبارية والدينية القصيرة (لتوفير التوكنز وتسريع البوت)
SAFE_SHORT_PHRASES = {
    "الله اكبر", "الله أكبر", "ما شاء الله", "ماشاء الله", "الحمد لله", "سبحان الله",
    "لا حول ولا قوة الا بالله", "يا رب", "يارب", "اللهم سلم", "عاجل", "تم", "مشكور",
    "احسنت", "أحسنت", "صح", "صحيح", "نعم", "لا", "اكيد", "أكيد", "ان شاء الله",
    "إن شاء الله", "حفظهم الله", "رحمه الله", "الله يرحمه", "امين", "آمين", "اللهم امين"
}

# 4. دالة الفحص الذكي للمنشن (@username)
def check_promotional_mention(text):
    mentions = re.findall(r"@([a-zA-Z0-9_]{4,32})", text)
    if not mentions:
        return None

    if re.search(r"\b(قناة|قناه|كروب|جروب|مجموعة|تابعوا|انضموا|اشتركوا|حسابي|خاص)\b", text, re.IGNORECASE):
        return "الدعوة لمتابعة حسابات أو قنوات خارجية"

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

# 5. دالة كشف الروابط المخفية تحت الكلمات (Hyperlinks / Entities)
def has_hidden_links(message):
    entities = (message.entities or []) + (message.caption_entities or [])
    for ent in entities:
        if ent.type in ['url', 'text_link']:
            return True
    return False

# 6. دالة فحص الإغراق المتتالي (Anti-Flood)
def check_is_flooding(chat_id, user_id):
    now = time.time()
    key = (chat_id, user_id)
    timestamps = user_message_timestamps.get(key, [])
    # الاحتفاظ فقط بالرسائل المرسلة خلال آخر 6 ثوانٍ
    timestamps = [t for t in timestamps if now - t < FLOOD_WINDOW]
    timestamps.append(now)
    user_message_timestamps[key] = timestamps

    if len(timestamps) > FLOOD_LIMIT:
        user_message_timestamps[key] = []  # تصفير بعد رصد الإغراق
        return True
    return False

# 7. تعليمات الذكاء الاصطناعي (Gemini)
SYSTEM_INSTRUCTION = """
أنت مشرف أمني في مجموعة نقاشات عسكرية وسياسية ساخنة حول أحداث الشرق الأوسط.
القاعدة الذهبية:
- النقاش الحاد، الجدل السياسي القوي، السخرية السياسية، وانتقاد الدول أو الحكومات أو الجيوش أو الفصائل مسموح تماماً ("ALLOW").
- عمل منشن لعضو آخر (@username) للرد عليه أو نقاشه هو أمر طبيعي ومسموح تماماً ("ALLOW").

مهمتك تصنيف الرسائل إلى 3 أحكام مع كتابة سبب واضح ومختصر باللغة العربية عند وجود مخالفة:

1. "RELIGIOUS_BAN" (حظر فوري نهائي):
- يستخدم حصراً في حالة: سب الله أو الذات الإلهية، سب الدين، سب الأنبياء والرسل، الاستهزاء بالقرآن أو الكتب السماوية، أو ازدراء الأديان والمقدسات.

2. "WARN" (حذف الرسالة + إعطاء تحذير مع ذكر السبب):
- الشتائم الشخصية المباشرة والألفاظ البذيئة أو القذف الموجه لعضو آخر (اكتب السبب مثلاً: "توجيه شتيمة شخصية وألفاظ غير لائقة لعضو آخر").
- التحقير الطائفي الحاد (اكتب السبب مثلاً: "إثارة النعرات الطائفية أو المذهبية").
- الرسائل الترويجية والإعلانات وطلب المراسلة في الخاص (اكتب السبب مثلاً: "نشر إعلانات أو ترويج غير مسموح به").

3. "ALLOW" (سماح وعدم تدخل):
- جميع النقاشات السياسية والعسكرية والأخبار والردود بين الأعضاء حتى لو كانت حادة.

أجب بصيغة JSON فقط بهذا الشكل الدقيق:
{"action": "ALLOW" | "WARN" | "RELIGIOUS_BAN", "reason": "اكتب سبب التحذير أو الحظر باختصار ووضوح هنا"}
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
        return f"@{user.username}"
    name = (user.first_name or "عضو").replace("[", "").replace("]", "").replace("*", "").replace("_", "")
    return f"[{name}](tg://user?id={user.id})"

# دالة الحظر الفوري
def execute_immediate_ban(message, user_mention, reason):
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass
    try:
        bot.ban_chat_member(message.chat.id, message.from_user.id)
        msg = bot.send_message(
            message.chat.id,
            f"🚫 *حظر فوري نهائي*\n"
            f"👤 العضو: {user_mention}\n"
            f"📌 *سبب الحظر:* {reason}.",
            parse_mode="Markdown"
        )
        delete_later(message.chat.id, msg.message_id, delay=25)
    except Exception as e:
        print(f"Immediate Ban error: {e}", flush=True)

# دالة الكتم المؤقت لمدة ساعة (تُستخدم عند الإغراق المتتالي أو بأمر المشرف)
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
            f"🔇 *تقييد مؤقت (كتم لمدة ساعة)*\n"
            f"👤 العضو: {user_mention}\n"
            f"📌 *السبب:* {reason}.",
            parse_mode="Markdown"
        )
        delete_later(message.chat.id, msg.message_id, delay=20)
    except Exception as e:
        print(f"Mute error: {e}", flush=True)

# دالة نظام التحذيرات الثلاثة (مع انتهاء صلاحية التحذير القديم بعد 24 ساعة)
def execute_warning(message, user_mention, reason, target_user_id=None):
    uid = target_user_id or message.from_user.id
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

    try:
        now = time.time()
        key = (message.chat.id, uid)
        # تصفية التحذيرات القديمة التي مر عليها أكثر من 24 ساعة
        valid_warnings = [t for t in user_warnings.get(key, []) if now - t < WARNING_EXPIRY_SECONDS]
        valid_warnings.append(now)
        user_warnings[key] = valid_warnings
        warnings_count = len(valid_warnings)

        if warnings_count >= 3:
            bot.ban_chat_member(message.chat.id, uid)
            user_warnings.pop(key, None)
            msg = bot.send_message(
                message.chat.id,
                f"🚫 *طرد وحظر نهائي (استنفاد التحذيرات 3/3)*\n"
                f"👤 العضو: {user_mention}\n"
                f"📌 *سبب المخالفة الأخيرة:* {reason}.",
                parse_mode="Markdown"
            )
        else:
            remaining = 3 - warnings_count
            msg = bot.send_message(
                message.chat.id,
                f"⚠️ *تنبيه وتحذير ({warnings_count}/3)*\n"
                f"👤 العضو: {user_mention}\n"
                f"📌 *لماذا تم تحذيرك وحذف رسالتك؟*\n"
                f"• السبب: *{reason}*.\n"
                f"⏳ متبقي لك ({remaining}) تحذير قبل الحظر التلقائي (تسقط التحذيرات بعد 24 ساعة من الالتزام).",
                parse_mode="Markdown"
            )
        delete_later(message.chat.id, msg.message_id, delay=20)
    except Exception as e:
        print(f"Warning execution error: {e}", flush=True)

# 8. معالج أوامر المشرفين السريعة بالرد (طرد / كتم / تحذير / عفو)
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

    # حذف رسالة أمر المشرف ليبقى الشات نظيفاً
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
            parse_mode="Markdown"
        )
        delete_later(message.chat.id, msg.message_id, delay=12)

    return True

# 9. الدالة الرئيسية لفحص الرسائل (الجديدة والمعدلة)
def process_moderation(message, is_edited=False):
    # 1. تجاهل منشورات القناة الرسمية التلقائية والرسائل المرسلة باسم القناة/المجموعة
    if getattr(message, 'is_automatic_forward', False) or message.sender_chat:
        return

    # 2. التحقق مما إذا كان المرسل مشرفاً (لتفعيل أوامر الإدارة وتجاهل فحص رسائله)
    try:
        member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in ['creator', 'administrator']:
            if not is_edited:
                handle_admin_commands(message)
            return
    except Exception:
        return

    user_mention = get_user_mention(message.from_user)

    # 3. فحص الإغراق المتتالي (Anti-Flood) للرسائل الجديدة فقط
    if not is_edited and check_is_flooding(message.chat.id, message.from_user.id):
        execute_mute_1h(message, message.from_user, "الإغراق المتتالي (إرسال رسائل متكررة بسرعة كبيرة)")
        return

    text = (message.text or message.caption or "").strip()

    # --- المرحلة أ: الفلتر البرمجي الفوري ---
    # 1. تحويل رسائل أو اقتباس من قنوات خارجية
    if message.forward_from_chat or getattr(message, 'external_reply', None):
        execute_warning(message, user_mention, "تحويل أو اقتباس رسائل من قنوات خارجية")
        return

    # 2. روابط صريحة أو مخفية تحت النص
    if has_hidden_links(message) or re.search(DIRECT_LINK_PATTERN, text, re.IGNORECASE):
        execute_warning(message, user_mention, "نشر روابط خارجية في المجموعة")
        return

    # 3. عبارات ترويجية أو إعلانية
    if re.search(PROMO_PHRASES_PATTERN, text, re.IGNORECASE):
        execute_warning(message, user_mention, "نشر عبارات ترويجية أو دعائية")
        return

    # 4. فحص المنشن الترويجي (@قناة أو @بوت)
    promo_mention_reason = check_promotional_mention(text)
    if promo_mention_reason:
        execute_warning(message, user_mention, promo_mention_reason)
        return

    if not text:
        return

    # 5. فحص سب الدين وازدراء الأديان -> حظر فوري بدون تحذيرات!
    for pattern in RELIGIOUS_INSULTS_WORDS:
        if re.search(pattern, text, re.IGNORECASE):
            execute_immediate_ban(message, user_mention, "سب الدين أو ازدراء المقدسات الدينية")
            return

    # 6. فحص الشتائم الشخصية والألفاظ النابية
    for pattern in PERSONAL_INSULTS_WORDS:
        if re.search(pattern, text, re.IGNORECASE):
            execute_warning(message, user_mention, "استخدام شتائم شخصية أو ألفاظ نابية في النقاش")
            return

    # 7. فحص الكلمات الطائفية الصريحة
    for pattern in SECTARIAN_WORDS:
        if re.search(pattern, text, re.IGNORECASE):
            execute_warning(message, user_mention, "استخدام مصطلحات طائفية ومذهبية مسيئة")
            return

    # 8. فحص الإغراق النصي الطويل جداً (أكثر من 1500 حرف)
    if len(text) > 1500:
        execute_warning(message, user_mention, "الإغراق النصي (إرسال نص طويل جداً يعطل النقاش)")
        return

    # --- المرحلة ب: الفلتر الموفر للتوكنز + الذكاء الاصطناعي (Gemini AI) ---
    # تنظيف النص من علامات الترقيم لفحص عدد الكلمات والعبارات الآمنة
    clean_text = re.sub(r"[^\w\s]", "", text).strip()
    words = clean_text.split()

    # إذا كانت الرسالة قصيرة جداً (أقل من 3 كلمات) أو من العبارات المعتادة، لا نرسلها لـ Gemini لتوفير الرصيد
    if len(words) < 3 or clean_text in SAFE_SHORT_PHRASES:
        return

    action, ai_reason = analyze_with_ai(text)
    if action == "RELIGIOUS_BAN":
        execute_immediate_ban(message, user_mention, ai_reason or "سب الدين وازدراء الأديان والمقدسات")
    elif action == "WARN":
        execute_warning(message, user_mention, ai_reason or "الإساءة اللفظية ومخالفة آداب النقاش")

# استقبال الرسائل الجديدة (نصوص، صور، فيديوهات، ملصقات، ملفات)
@bot.message_handler(
    func=lambda m: m.chat.type in ['group', 'supergroup'],
    content_types=['text', 'photo', 'video', 'document', 'sticker', 'animation']
)
def handle_new_message(message):
    process_moderation(message, is_edited=False)

# استقبال الرسائل المعدلة (Edited Messages) لكشف التحايل بتعديل الرسالة بعد إرسالها
@bot.edited_message_handler(
    func=lambda m: m.chat.type in ['group', 'supergroup'],
    content_types=['text', 'photo', 'video', 'document']
)
def handle_edited_message(message):
    process_moderation(message, is_edited=True)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    print("Warleaks AI Guard v8 (Ultimate Edition) is running...", flush=True)
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)
    
