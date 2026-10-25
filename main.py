import os, re, time, html, traceback, threading
from collections import deque
from flask import Flask, request, session, redirect, url_for, render_template_string
import telebot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
LOG_CHAT_ID = os.environ.get("LOG_CHAT_ID", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123").strip()
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=True, num_threads=8)

user_warnings, user_msg_ts, username_cache, known_chats = {}, {}, {}, {}
active_log_chat_id = int(LOG_CHAT_ID) if LOG_CHAT_ID.lstrip("-").isdigit() else None
stats = {"scanned": 0, "warnings": 0, "mutes": 0, "bans": 0, "start": time.strftime('%H:%M')}
recent_logs = deque(maxlen=40)
custom_words = set()

app = Flask(__name__)
app.secret_key = "warleaks-2026"

HTML_TPL = """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>لوحة التحكم</title>
<style>body{background:#0f172a;color:#f8fafc;font-family:system-ui;padding:15px;margin:0}.c{max-width:980px;margin:auto}.box{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:14px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:14px}.val{font-size:1.5rem;font-weight:700;margin-top:4px}
input,select,button{padding:8px 10px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff}button{background:#3b82f6;border:none;font-weight:600;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:.85rem}th,td{padding:8px;border-bottom:1px solid #334155;text-align:right}</style></head><body><div class="c">
{% if not auth %}<div class="box" style="max-width:320px;margin:70px auto;text-align:center"><h3>دخول المشرف</h3><form method="POST" action="/login">
<input type="password" name="p" placeholder="كلمة المرور" required style="width:90%;margin:10px 0"><button style="width:95%">دخول</button></form></div>
{% else %}<div class="box" style="display:flex;justify-content:space-between;align-items:center"><b>حارس التسريبات v18 | السجل: {{ log_id or 'غير مربوط' }}</b>
<div><a href="/"><button>تحديث</button></a> <a href="/logout"><button style="background:#ef4444">خروج</button></a></div></div>
<div class="grid"><div class="box">المفحوصة<div class="val">{{ st.scanned }}</div></div><div class="box" style="color:#f59e0b">التحذيرات<div class="val">{{ st.warnings }}</div></div>
<div class="box" style="color:#3b82f6">الكتم<div class="val">{{ st.mutes }}</div></div><div class="box" style="color:#ef4444">الحظر<div class="val">{{ st.bans }}</div></div></div>
<div class="box"><form method="POST" action="/word" style="display:flex;gap:6px"><input name="w" placeholder="إضافة كلمة ممنوعة..." required style="flex:1"><button>إضافة</button></form>
<div style="margin-top:8px">{% for w in cw %}<span style="background:#0f172a;padding:3px 8px;border-radius:4px;margin-left:5px;font-size:.82rem">{{ w }} <a href="/delw?w={{ w }}" style="color:#ef4444;text-decoration:none">✕</a></span>{% endfor %}</div></div>
<div class="box"><form method="POST" action="/act" style="display:flex;gap:6px;flex-wrap:wrap">
<select name="cid" required>{% for id,t in chats.items() %}<option value="{{ id }}">{{ t }}</option>{% endfor %}</select>
<input name="u" placeholder="ID أو @username" required><select name="a"><option value="unban">عفو وفك الكتم/الحظر</option><option value="m1">كتم ساعة</option><option value="m24">كتم 24 ساعة</option><option value="ban">حظر نهائي</option></select><button>تنفيذ</button></form></div>
<div class="box" style="overflow-x:auto"><table><thead><tr><th>الوقت</th><th>العضو</th><th>الإجراء</th><th>السبب</th><th>الرسالة</th><th>تحكم</th></tr></thead><tbody>
{% for l in logs %}<tr><td>{{ l.t }}</td><td>{{ l.u }}<br><code>{{ l.uid }}</code></td><td>{{ l.a }}</td><td>{{ l.r }}</td><td>{{ l.txt }}</td>
<td><form method="POST" action="/act"><input type="hidden" name="cid" value="{{ l.cid }}"><input type="hidden" name="u" value="{{ l.uid }}"><input type="hidden" name="a" value="unban"><button style="background:#10b981;padding:4px 8px">عفو</button></form></td></tr>{% endfor %}
</tbody></table></div>{% endif %}</div></body></html>"""

@app.route('/ping')
def ping(): return "OK", 200

@app.route('/')
def index():
    return render_template_string(HTML_TPL, auth=session.get("ok"), st=stats, logs=list(recent_logs), cw=list(custom_words), chats=known_chats, log_id=active_log_chat_id)

@app.route('/login', methods=['POST'])
def login():
    if request.form.get("p", "").strip() == ADMIN_PASSWORD: session["ok"] = True
    return redirect(url_for('index'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))

@app.route('/word', methods=['POST'])
def add_w():
    if session.get("ok") and request.form.get("w"): custom_words.add(norm_ar(request.form.get("w").strip()))
    return redirect(url_for('index'))

@app.route('/delw')
def del_w():
    if session.get("ok"): custom_words.discard(request.args.get("w", ""))
    return redirect(url_for('index'))

@app.route('/act', methods=['POST'])
def manual_act():
    if not session.get("ok"): return redirect(url_for('index'))
    try:
        cid, u_str, a = int(request.form.get("cid")), request.form.get("u", "").strip(), request.form.get("a")
        uid = username_cache.get(u_str[1:].lower()) if u_str.startswith("@") else int(u_str)
        if a == "unban": pardon_user(cid, uid, f"العضو ({uid})", True)
        elif a == "m1": bot.restrict_chat_member(cid, uid, until_date=int(time.time())+3600, can_send_messages=False)
        elif a == "m24": bot.restrict_chat_member(cid, uid, until_date=int(time.time())+86400, can_send_messages=False)
        elif a == "ban": bot.ban_chat_member(cid, uid)
    except Exception: pass
    return redirect(url_for('index'))

threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), use_reloader=False), daemon=True).start()

def norm_ar(t):
    if not t: return ""
    t = re.sub(r"[\u064B-\u065F\u0640]", "", t)
    t = re.sub(r"[أإآ]", "ا", re.sub(r"ة", "ه", re.sub(r"ى", "ي", t)))
    t = re.sub(r"(?<=[ء-ي])[\.\-\_\*\,]+(?=[ء-ي])", "", t)
    return re.sub(r"(.)\1{2,}", r"\1", t)

P_REL = r"\b(يلعن|ينعل|نعل|العن|سب|يسب|كسم|كس\s*ام|نيك)\s*(دين|رب|اله|رسول|نبي|قران|اسلام|الله)(ك|كم|هم|ه|ها)?\b"
P_PROF = r"\b(كسمك|كسمكم|كسمه|كسمها|كس\s*ام|كس\s*اخت|منيوك|منيك|متناك|شرموط|شرموطه|قحبه|عرص|عرصا|معرص|قواد|ديوث|مخنث|طيزك|زبي)\b"
P_INS = r"\b(يا|انت|انتم|انتو)\s*(حمار|غبي|كلب|حيوان|بهيم|بغل|سافل|نذل|قذر|واطي|زباله|تافه|احمق|معتوه|منحط|رخيص|جبان|لقيط)\b"
P_SEC = r"\b(كس\s*ام|كسم|يلعن|ينعل|كلاب|انجاس|اولاد\s*متعه)\s*(الشيعه|السنه|الروافض|النواصب|المجوس|العلويين|الدروز|الاكراد)\b"
P_TOP = r"(تداول|التداول|فوركس|بيتكوين|كريبتو|استثمار|يستثمر|دخل\s*يومي|ربح\s*يومي|ارباح|توصيات|قناتنا|قناتي)"
P_ACT = r"(يراسلني|راسلني|راسلوني|يتواصل|تواصل|خاص|انضموا|اشتركوا|تابعوا|مجاني|مضمون|يعطيه\s*الطريقه|جواله)"
P_LINK = r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+|\b[a-zA-Z0-9-]+\.(com|net|org|io|me|co)\b)"

def del_msg(cid, mid, d=0):
    def task():
        if d: time.sleep(d)
        try: bot.delete_message(cid, mid)
        except Exception: pass
    threading.Thread(target=task, daemon=True).start()

def u_ment(u): return f"@{html.escape(u.username)}" if (u and u.username) else f'<a href="tg://user?id={u.id if u else 0}">{html.escape(u.first_name if u and u.first_name else "عضو")}</a>'

def log_ev(act, chat, uid, ment, rsn, txt=""):
    ts = time.strftime('%H:%M:%S', time.gmtime(time.time()+3600))
    recent_logs.appendleft({"t": ts, "cid": chat.id, "u": re.sub(r"<[^>]+>", "", ment), "uid": uid, "a": act, "r": rsn, "txt": txt[:150]})
    if active_log_chat_id:
        try: bot.send_message(active_log_chat_id, f"📋 <b>{act}</b>\n🏠 {html.escape(chat.title or '')}\n👤 {ment} (<code>{uid}</code>)\n📌 <b>السبب:</b> {html.escape(rsn)}\n💬 <pre>{html.escape(txt[:400])}</pre>\n💡 <code>الغاء الكتم {uid}</code>", parse_mode="HTML")
        except Exception: pass

def do_ban(m, ment, rsn, uid=None, del_m=True):
    stats["bans"] += 1; t_id = uid or m.from_user.id; txt = (m.text or m.caption or "") if m else ""
    if del_m and m: del_msg(m.chat.id, m.message_id)
    try:
        bot.ban_chat_member(m.chat.id, t_id)
        s = bot.send_message(m.chat.id, f"🚫 <b>حظر فوري نهائي</b>\n👤 العضو: {ment}\n📌 <b>السبب:</b> {html.escape(rsn)}", parse_mode="HTML")
        del_msg(m.chat.id, s.message_id, 20); log_ev("حظر نهائي 🚫", m.chat, t_id, ment, rsn, txt)
    except Exception: pass

def do_mute(m, t_id, ment, rsn, sec=3600, del_m=True):
    stats["mutes"] += 1; txt = (m.text or m.caption or "") if (del_m and m) else ""
    if del_m and m: del_msg(m.chat.id, m.message_id)
    sec = max(60, min(int(sec), 365*86400))
    try:
        bot.restrict_chat_member(m.chat.id, t_id, until_date=int(time.time())+sec, can_send_messages=False)
        s = bot.send_message(m.chat.id, f"🔇 <b>كتم مؤقت ({int(sec/3600) or 1} ساعة)</b>\n👤 العضو: {ment}\n📌 <b>السبب:</b> {html.escape(rsn)}", parse_mode="HTML")
        del_msg(m.chat.id, s.message_id, 20); log_ev("كتم مؤقت 🔇", m.chat, t_id, ment, rsn, txt)
    except Exception: pass

def pardon_user(cid, uid, ment, unban=True):
    user_warnings.pop((cid, uid), None)
    try:
        if unban: bot.unban_chat_member(cid, uid, only_if_banned=True)
        bot.restrict_chat_member(cid, uid, can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)
        s = bot.send_message(cid, f"✅ تم إلغاء العقوبة وتصفير التحذيرات لـ {ment}.", parse_mode="HTML")
        del_msg(cid, s.message_id, 12)
    except Exception: pass

def do_warn(m, ment, rsn, uid=None, del_m=True):
    stats["warnings"] += 1; t_id = uid or m.from_user.id; txt = (m.text or m.caption or "") if (del_m and m) else ""
    if del_m and m: del_msg(m.chat.id, m.message_id)
    now = time.time(); k = (m.chat.id, t_id)
    vw = [t for t in user_warnings.get(k, []) if now - t < 86400] + [now]
    user_warnings[k] = vw; c = len(vw)
    try:
        if c >= 3:
            stats["bans"] += 1; bot.ban_chat_member(m.chat.id, t_id); user_warnings.pop(k, None)
            s = bot.send_message(m.chat.id, f"🚫 <b>حظر نهائي (3/3 تحذيرات)</b>\n👤 {ment}\n📌 <b>السبب:</b> {html.escape(rsn)}", parse_mode="HTML")
            log_ev("حظر (3/3) 🚫", m.chat, t_id, ment, rsn, txt)
        else:
            s = bot.send_message(m.chat.id, f"⚠️ <b>تحذير ({c}/3)</b> لـ {ment}\n📌 <b>السبب:</b> {html.escape(rsn)}", parse_mode="HTML")
            log_ev(f"تحذير ({c}/3) ⚠️", m.chat, t_id, ment, rsn, txt)
        del_msg(m.chat.id, s.message_id, 20)
    except Exception: pass

def handle_adm(m):
    if not m.text: return False
    raw = m.text.strip().lstrip("/")
    cmd = norm_ar(re.sub(r"(@\w+|\b\d+(?:\.\d+)?\s*(?:د|س|ي|ساعه|ساعات|يوم)?\b)", "", raw).strip()).lower()
    UNM, MUT, BAN, WRN = {"الغاء الكتم", "فك الكتم", "عفو", "فك الحظر", "الغاء الحظر", "unmute", "unban"}, {"كتم", "mute"}, {"طرد", "حظر", "ban"}, {"تحذير", "warn"}
    if cmd not in (UNM | MUT | BAN | WRN): return False
    del_msg(m.chat.id, m.message_id)
    rep = m.reply_to_message
    uid, ment = (rep.from_user.id, u_ment(rep.from_user)) if (rep and rep.from_user) else (None, None)
    if not uid:
        um = re.search(r"@(\w+)", raw)
        if um: uid = username_cache.get(um.group(1).lower()); ment = f"@{um.group(1)}"
        im = re.search(r"\b(\d{6,15})\b", raw)
        if im and not uid: uid = int(im.group(1)); ment = f"العضو ({uid})"
    if not uid: return True
    if cmd in UNM: pardon_user(m.chat.id, uid, ment, True)
    elif cmd in MUT:
        dm = re.search(r"\b(\d+)\b", re.sub(r"(@\w+|\b\d{6,15}\b)", "", raw))
        do_mute(rep or m, uid, ment, "قرار إداري", (int(dm.group(1))*3600 if dm else 3600), bool(rep))
    elif cmd in BAN: do_ban(rep or m, ment, "قرار إداري", uid, bool(rep))
    elif cmd in WRN: do_warn(rep or m, ment, "تنبيه إداري", uid, bool(rep))
    return True

def process_msg(m, ed=False):
    global active_log_chat_id
    stats["scanned"] += 1
    if m.chat and m.chat.title: known_chats[m.chat.id] = m.chat.title
    if m.from_user and m.from_user.username: username_cache[m.from_user.username.lower()] = m.from_user.id
    raw = (m.text or m.caption or "").strip(); norm = norm_ar(raw)
    if not ed and norm in {"تفعيل السجل", "ربط السجل", "setlog"}:
        active_log_chat_id = m.chat.id; bot.reply_to(m, f"✅ <b>تم تفعيل السجل هنا!</b>\n<code>{m.chat.id}</code>", parse_mode="HTML"); return
    if not ed and norm in {"فحص", "ping"}:
        bot.reply_to(m, f"⚡ <b>حارس التسريبات v18 يعمل!</b>\nالسجل: <code>{active_log_chat_id}</code>", parse_mode="HTML"); return
    if getattr(m, 'is_automatic_forward', False): return
    is_adm = (m.sender_chat and m.sender_chat.id == m.chat.id)
    if not is_adm and m.from_user:
        try: is_adm = bot.get_chat_member(m.chat.id, m.from_user.id).status in ['creator', 'administrator']
        except Exception: pass
    if is_adm:
        if not ed: handle_adm(m)
        return
    if active_log_chat_id and m.chat.id == active_log_chat_id: return
    ment = u_ment(m.from_user)
    now = time.time(); fk = (m.chat.id, m.from_user.id)
    ts = [t for t in user_msg_ts.get(fk, []) if now - t < 6] + [now]; user_msg_ts[fk] = ts
    if not ed and len(ts) > 5: user_msg_ts[fk] = []; do_mute(m, m.from_user.id, ment, "إغراق متتالي (Flood)"); return
    if m.forward_from_chat or getattr(m, 'external_reply', None): do_warn(m, ment, "تحويل من قناة خارجية"); return
    if any(e.type in ['url', 'text_link'] for e in (m.entities or [])+(m.caption_entities or [])) or re.search(P_LINK, raw, re.I): do_warn(m, ment, "نشر روابط خارجية"); return
    if not raw: return
    for cw in list(custom_words):
        if cw and cw in norm: do_warn(m, ment, f"كلمة ممنوعة ({cw})"); return
    if (re.search(P_TOP, norm) and re.search(P_ACT, norm)) or (re.findall(r"@\w+", raw) and (re.search(P_TOP, norm) or re.search(P_ACT, norm))):
        do_warn(m, ment, "إعلان تداول أو ترويج لحسابات"); return
    if re.search(P_REL, norm, re.I): do_ban(m, ment, "سب الدين أو المقدسات"); return
    if re.search(P_PROF, norm, re.I): do_warn(m, ment, "ألفاظ فاحشة أو قذف أعراض"); return
    if re.search(P_INS, norm, re.I): do_warn(m, ment, "شتيمة شخصية مباشرة"); return
    if re.search(P_SEC, norm, re.I): do_warn(m, ment, "إثارة نعرات طائفية"); return
    if len(raw) > 1500: do_warn(m, ment, "إغراق نصي طويل"); return

@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['text', 'photo', 'video', 'document', 'sticker', 'animation'])
def h_new(m): process_msg(m, False)

@bot.edited_message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['text', 'photo', 'video', 'document'])
def h_edit(m): process_msg(m, True)

if __name__ == "__main__":
    while True:
        try: bot.remove_webhook(); bot.infinity_polling(skip_pending=True, timeout=20)
        except Exception: time.sleep(5)
# === END ===
