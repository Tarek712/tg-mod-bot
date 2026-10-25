import os
import re
import json
import time
import threading
from flask import Flask
import telebot
from google import genai
from google.genai import types

# 1. سيرفر الويب الوهمي لإبقاء Render مستيقظاً 24/7
app = Flask(__name__)

@app.route('/')
def home():
    return "Warleaks AI Guard is Active 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# 2. جلب المفاتيح من إعدادات البيئة في Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

# 3. القاموس البرمجي الفوري (يرصد المخالفات الصريحة في 0.01 ثانية بدون استهلاك API)
# أ) روابط وترويج وأرقام هواتف -> طرد نهائي
LINK_AND_PROMO_PATTERN = r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+|@\w+|واتساب|راسلوني خاص|انضموا لقناة)"

# ب) قاموس الطائفية والشتائم الجسيمة الصريحة -> طرد نهائي
SEVERE_AND_SECTARIAN_WORDS = [
    r"ابن القحبة", r"ولد القحبة", r"شرموط", r"منيوك", r"نيك امك", r"كسمك", r"كس اختك",
    r"يلعن دين", r"ينعل دين", r"ينعل رب", r"روافض", r"رافضي", r"نواصب", r"ناصبي", r"مجوس"
]

# ج) قاموس الشتائم العادية الصريحة -> كتم لمدة ساعة
MILD_INSULTS_WORDS = [
    r"\bيا غبي\b", r"\bيا حمار\b", r"\bيا كلب\b", r"\bيا حيوان\b", r"\bيا تافه\b",
    r"\bيا بهيم\b", r"\bيا بغل\b", r"\bيا سافل\b", r"\bيا نذل\b", r"\bيا منبطح\b"
]

# 4. الدماغ الذكي (Gemini) لتحليل السياق، اللهجات، والشتائم المبطنة أو المقطعة
SYSTEM_INSTRUCTION = """
أنت المشرف الأمني الصامت لمجموعة نقاشات إخبارية وعسكرية تغطي أحداث الشرق الأوسط.
مهمتك الوحيدة قراءة تعليقات الأعضاء بجميع اللهجات العربية (الدارجة المغاربية، الشامية، المصرية، الخليجية، العراقية، والفرانكو) وإصدار حكم صارم بناءً على القوانين التالية:

⚠️ ملاحظة سياقية هامة جداً:
المجموعة تناقش أخبار الحروب والسياسة في الشرق الأوسط. استخدام مصطلحات عسكرية (مثل: قصف، صواريخ، اغتيال، تدمير، هجوم، جيش، مقاومة، احتلال) في سياق تحليل الخبر هو أمر طبيعي ومسموح تماماً ("ALLOW") طالما لم يوجه كشتيمة شخصية لعضو آخر أو تحريض طائفي.

تصنيف العقوبات الصارم:

1. "BAN" (حظر وطرد نهائي من المجموعة):
- الشتائم غير العادية والجسيمة: سب الدين أو الذات الإلهية، قذف الأمهات والأعراض والشرف، الكلمات الجنسية الفاحشة الصريحة أو المبطنة أو المكتوبة بحروف مقطعة.
- الطائفية والعنصرية والفتنة: التكفير، السب المذهبي أو الطائفي (سنة، شيعة، دروز، مسيحيين، كرد، عرب، أمازيغ...)، أو إهانة الشعوب والبلدان العربية والإسلامية وإثارة الكراهية بينها.
- الترويج والاحتيال: أي إعلان تجاري، ترويج لحسابات أو قنوات، طلب مراسلة في الخاص، أو نصب.
- التهديد الشخصي المباشر لأعضاء المجموعة.

2. "MUTE_1H" (حذف الرسالة + كتم لمدة ساعة واحدة):
- الشتائم العادية والانفعالية الموجهة للأعضاء: (مثل: غبي، حمار، كلب، جاهل، مطبل، ذباب إلكتروني، خائن، عميل، كذاب، أحمق، مسطول، إلخ).
- الاستفزاز الشخصي والسخرية المهينة من عضو آخر بدلاً من مناقشة الخبر.
- الإغراق النصي (Spam) أو كتابة طلاسم وحروف مكررة لتخريب النقاش.

3. "ALLOW" (سماح وعدم تدخل):
- النقاش السياسي والعسكري الطبيعي، إبداء الرأي حول الأخبار، الأدعية، أو الأسئلة العادية حتى لو كان فيها اختلاف حاد في الرأي السياسي بدون مشخصنة أو سب.

أجب بصيغة JSON فقط وبدون أي نص إضافي:
{"action": "ALLOW" | "MUTE_1H" | "BAN"}
"""

# دالة لحذف إشعار العقوبة بعد 8 ثوانٍ ليبقى النقاش نظيفاً
def delete_later(chat_id, message_id, delay=8):
    def task():
        time.sleep(delay)
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
    threading.Thread(target=task, daemon=True).start()

# دوال تنفيذ العقوبات
def execute_ban(message, user_mention, reason):
    try:
        bot.delete_message(message.chat.id, message.message_id)
        bot.ban_chat_member(message.chat.id, message.from_user.id)
        msg = bot.send_message(message.chat.id, f"🚫 تم طرد وحظر {user_mention} نهائياً ({reason}).")
        delete_later(message.chat.id, msg.message_id)
    except Exception as e:
        print(f"Ban execution error: {e}")

def execute_mute_1h(message, user_mention, reason):
    try:
        bot.delete_message(message.chat.id, message.message_id)
        # كتم لمدة ساعة واحدة (3600 ثانية)
        bot.restrict_chat_member(
            message.chat.id,
            message.from_user.id,
            until_date=int(time.time()) + 3600,
            can_send_messages=False
        )
        msg = bot.send_message(message.chat.id, f"🔇 تم حذف الرسالة وكتم {user_mention} لمدة ساعة ({reason}).")
        delete_later(message.chat.id, msg.message_id)
    except Exception as e:
        print(f"Mute execution error: {e}")

@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['text'])
def moderate_group(message):
    # 1. السماح التلقائي لمنشورات القناة الرسمية المرتبطة بالمجموعة
    if getattr(message, 'is_automatic_forward', False) or (message.sender_chat and message.sender_chat.id == message.chat.id):
        return

    # 2. حماية المشرفين ومؤسس المجموعة
    try:
        member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in ['creator', 'administrator']:
            return
    except Exception:
        return

    text = message.text.strip()
    if not text:
        return

    user_mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name

    # --- المرحلة أ: الفلتر البرمجي الفوري (بدون استهلاك API) ---
    # 1. فحص الرسائل المحولة من قنوات خارجية أو الروابط والترويج -> حظر نهائي
    if message.forward_from or message.forward_from_chat or re.search(LINK_AND_PROMO_PATTERN, text, re.IGNORECASE):
        execute_ban(message, user_mention, "نشر روابط أو ترويج")
        return

    # 2. فحص القاموس الفوري للشتائم الجسيمة والطائفية -> حظر نهائي
    for pattern in SEVERE_AND_SECTARIAN_WORDS:
        if re.search(pattern, text, re.IGNORECASE):
            execute_ban(message, user_mention, "مخالفة جسيمة / طائفية")
            return

    # 3. فحص القاموس الفوري للشتائم العادية -> كتم لمدة ساعة
    for pattern in MILD_INSULTS_WORDS:
        if re.search(pattern, text, re.IGNORECASE):
            execute_mute_1h(message, user_mention, "ألفاظ غير لائقة")
            return

    # 4. فحص الإغراق النصي الطويل جداً (أكثر من 1200 حرف في تعليق واحد من عضو عادي) -> كتم ساعة
    if len(text) > 1200:
        execute_mute_1h(message, user_mention, "إغراق نصي / سبام")
        return

    # --- المرحلة ب: الفلتر الذكي (Gemini AI) لكشف الشتائم المبطنة والطائفية واللهجات ---
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0.0,
            )
        )

        result = json.loads(response.text)
        action = result.get("action", "ALLOW")

        if action == "BAN":
            execute_ban(message, user_mention, "مخالفة جسيمة / طائفية / ترويج")
        elif action == "MUTE_1H":
            execute_mute_1h(message, user_mention, "شتيمة أو إساءة في النقاش")

    except Exception as e:
        print(f"AI filter error: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    print("Warleaks AI Guard is running...")
    bot.infinity_polling(skip_pending=True)
