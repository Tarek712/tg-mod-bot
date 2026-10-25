import os
import re
import json
import time
import threading
from flask import Flask
import telebot
from google import genai
from google.genai import types

app = Flask(__name__)

@app.route('/')
def home():
    return "Text-Only AI Moderator is Active 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = """
أنت مشرف أمني صامت لمجموعة نقاشات نصية على تلغرام. لا ترد على الأسئلة ولا تتحدث أبداً.
مهمتك الوحيدة قراءة النص بجميع اللهجات (الدارجة الجزائرية والمغاربية، العربية، الفرنسية، الإنجليزية، والفرانكو) وإصدار حكم واحد:

1. "BAN": إذا كانت الرسالة إعلاناً، ترويجاً لقناة أو حساب، بيع وشراء غير مصرح، نصب واحتيال، أو سبام متكرر.
2. "MUTE": إذا كانت الرسالة تحتوي على سب، شتم، كلام بذيء، إهانة شخصية، تنمر، أو تحريض، حتى لو كُتبت بحروف مقطعة أو مشفرة.
3. "ALLOW": إذا كانت الرسالة نقاشاً عادياً أو سؤالاً طبيعياً لا يخالف القوانين.

أجب بصيغة JSON فقط بهذا الشكل:
{"action": "ALLOW" | "MUTE" | "BAN"}
"""

def delete_later(chat_id, message_id, delay=8):
    def task():
        time.sleep(delay)
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
    threading.Thread(target=task, daemon=True).start()

@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['text'])
def text_enforcer(message):
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

    # الفلتر السريع: الروابط والرسائل المحولة من قنوات
    link_pattern = r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+)"
    if re.search(link_pattern, text, re.IGNORECASE) or message.forward_from or message.forward_from_chat:
        try:
            bot.delete_message(message.chat.id, message.message_id)
            bot.ban_chat_member(message.chat.id, message.from_user.id)
            msg = bot.send_message(message.chat.id, f"🚫 تم حظر {user_mention} لنشر روابط أو تحويل رسائل.")
            delete_later(message.chat.id, msg.message_id)
            return
        except Exception as e:
            print(f"Fast-filter error: {e}")
            return

    # فلتر الذكاء الاصطناعي: فحص المعنى والشتائم والإعلانات النصية
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
            bot.delete_message(message.chat.id, message.message_id)
            bot.ban_chat_member(message.chat.id, message.from_user.id)
            msg = bot.send_message(message.chat.id, f"🚫 تم طرد {user_mention} نهائياً (إعلان/سبام).")
            delete_later(message.chat.id, msg.message_id)

        elif action == "MUTE":
            bot.delete_message(message.chat.id, message.message_id)
            bot.restrict_chat_member(
                message.chat.id,
                message.from_user.id,
                until_date=int(time.time()) + 86400,
                can_send_messages=False
            )
            msg = bot.send_message(message.chat.id, f"🔇 تم حذف الرسالة وكتم {user_mention} لمدة 24 ساعة.")
            delete_later(message.chat.id, msg.message_id)

    except Exception as e:
        print(f"AI filter error: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    print("Text-Only Silent Enforcer is running...")
    bot.infinity_polling(skip_pending=True)
  
