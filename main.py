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
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

# قائمة أحدث نماذج جيميناي (للتبديل التلقائي وضمان عدم التوقف)
MODELS_TO_TRY = [
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash"
]

# 3. القاموس البرمجي الفوري (يرصد المخالفات الصريحة بدون استهلاك API)
# أ) روابط وترويج وأرقام هواتف -> طرد نهائي
LINK_AND_PROMO_PATTERN = r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+|واتساب|راسلوني خاص|انضموا لقناة)"

# ب) قاموس الطائفية والشتائم الجسيمة -> طرد نهائي
SEVERE_AND_SECTARIAN_WORDS = [
    r"قحبة", r"شرموط", r"منيوك", r"نيك", r"كسمك", r"كس اختك",
    r"يلعن دين", r"ينعل دين", r"ينعل رب", r"روافض", r"رافضي", r"نواصب", r"ناصبي", r"مجوس"
]

# ج) قاموس الشتائم العادية -> كتم لمدة ساعة
MILD_INSULTS_WORDS = [
    r"حمار", r"غبي", r"كلب", r"حيوان", r"تافه",
    r"بهيم", r"بغل", r"سافل", r"نذل", r"منبطح", r"احمق", r"أحمق"
]

# 4. الدماغ الذكي (Gemini) لتحليل السياق واللهجات
SYSTEM_INSTRUCTION = """
أنت المشرف الأمني الصامت لمجموعة نقاشات إخبارية وعسكرية تغطي أحداث الشرق الأوسط.
مهمتك قراءة تعليقات الأعضاء بجميع اللهجات العربية والفرانكو وإصدار حكم صارم:

- المصطلحات العسكرية (قصف، صواريخ، اغتيال، تدمير، هجوم، جيش، مقاومة) في سياق الخبر مسموحة تماماً ("ALLOW").
- "BAN": الشتائم الجسيمة (سب الدين، الأعراض، الأمهات)، الطائفية والعنصرية (سنة، شيعة، تكفير، إهانة الشعوب)، أو الترويج والاحتيال.
- "MUTE_1H": الشتائم العادية الموجهة للأعضاء (غبي، حمار، كلب، مطبل، ذباب، خائن، عميل، كذاب) أو الاستفزاز الشخصي.
- "ALLOW": النقاش السياسي والعسكري الطبيعي.

أجب بصيغة JSON فقط:
{"action": "ALLOW" | "MUTE_1H" | "BAN"}
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
            return result.get("action", "ALLOW")
        except Exception as e:
            print(f"Model {model_name} failed: {e}", flush=True)
            continue
    return "ALLOW"

# دالة لحذف إشعار العقوبة بعد 8 ثوانٍ ليبقى الشات نظيفاً
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
        print(f"Ban error: {e}", flush=True)

def execute_mute_1h(message, user_mention, reason):
    try:
        bot.delete_message(message.chat.id, message.message_id)
        bot.restrict_chat_member(
            message.chat.id,
            message.from_user.id,
            until_date=int(time.time()) + 3600,
            can_send_messages=False
        )
        msg = bot.send_message(message.chat.id, f"🔇 تم حذف الرسالة وكتم {user_mention} لمدة ساعة ({reason}).")
        delete_later(message.chat.id, msg.message_id)
    except Exception as e:
        print(f"Mute error: {e}", flush=True)

@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['text'])
def moderate_group(message):
    # 1. تجاهل منشورات القناة الرسمية التلقائية والرسائل المرسلة باسم القناة/المجموعة
    if getattr(message, 'is_automatic_forward', False) or message.sender_chat:
        return

    # 2. حماية المشرفين ومالك المجموعة (يتجاهل رسائلهم تماماً ولا يفحصها)
    try:
        member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in ['creator', 'administrator']:
            return
    except Exception as e:
        print(f"Admin check error: {e}", flush=True)
        return

    text = message.text.strip()
    if not text:
        return

    user_mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name

    # --- المرحلة أ: الفلتر البرمجي الفوري ---
    # 1. فحص الرسائل المحولة من قنوات خارجية أو الروابط والترويج -> حظر نهائي
    if message.forward_from or message.forward_from_chat or re.search(LINK_AND_PROMO_PATTERN, text, re.IGNORECASE):
        execute_ban(message, user_mention, "نشر روابط أو ترويج")
        return

    # 2. فحص الشتائم الجسيمة والطائفية -> حظر نهائي
    for pattern in SEVERE_AND_SECTARIAN_WORDS:
        if re.search(pattern, text, re.IGNORECASE):
            execute_ban(message, user_mention, "مخالفة جسيمة / طائفية")
            return

    # 3. فحص الشتائم العادية -> كتم لمدة ساعة
    for pattern in MILD_INSULTS_WORDS:
        if re.search(pattern, text, re.IGNORECASE):
            execute_mute_1h(message, user_mention, "ألفاظ غير لائقة")
            return

    # 4. فحص الإغراق النصي الطويل جداً (أكثر من 1200 حرف) -> كتم لمدة ساعة
    if len(text) > 1200:
        execute_mute_1h(message, user_mention, "إغراق نصي / سبام")
        return

    # --- المرحلة ب: الفلتر الذكي (Gemini AI) ---
    action = analyze_with_ai(text)
    if action == "BAN":
        execute_ban(message, user_mention, "مخالفة جسيمة / طائفية / ترويج")
    elif action == "MUTE_1H":
        execute_mute_1h(message, user_mention, "شتيمة أو إساءة في النقاش")

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    print("Warleaks AI Guard Final is running...", flush=True)
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)
    
