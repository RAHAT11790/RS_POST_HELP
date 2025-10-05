import os
import json
import threading
from flask import Flask
from telegram import (
    Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackQueryHandler, CallbackContext
)

# ========================
# 🔹 CONFIGURATION
# ========================
TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_FILE = "channels.json"
POST_FILE = "posts.json"

# ========================
# 🔹 FILE UTILITIES
# ========================
def load_json(file):
    if not os.path.exists(file):
        return []
    with open(file, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return []

def save_json(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def ensure_files():
    if not os.path.exists(CHANNEL_FILE):
        save_json(CHANNEL_FILE, [])
    if not os.path.exists(POST_FILE):
        save_json(POST_FILE, [])

# ========================
# 🔹 BUTTON PARSER
# ========================
def parse_buttons(text):
    if not text:
        return None
    rows = []
    for line in text.splitlines():
        btns = []
        for part in line.split("&&"):
            if "-" in part:
                title, url = part.split("-", 1)
                btns.append(InlineKeyboardButton(title.strip(), url=url.strip()))
        if btns:
            rows.append(btns)
    return InlineKeyboardMarkup(rows)

# ========================
# 🔹 MENU BUTTONS
# ========================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Channel", callback_data="add_channel")],
        [InlineKeyboardButton("📝 Create Post", callback_data="create_post")],
        [InlineKeyboardButton("📤 Send Post", callback_data="send_post")],
        [InlineKeyboardButton("📘 Button Guide", callback_data="guide")]
    ])

def back_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Back", callback_data="back")]])

# ========================
# 🔹 COMMAND: START
# ========================
def start(update: Update, ctx: CallbackContext):
    update.message.reply_text(
        "👋 স্বাগতম!\n\nএই বট দিয়ে মিডিয়া + বাটন সহ পোস্ট তৈরি ও চ্যানেলে পাঠানো যাবে।",
        reply_markup=main_menu()
    )

# ========================
# 🔹 ADD CHANNEL
# ========================
def add_channel_cb(update, ctx):
    q = update.callback_query
    q.answer()
    q.message.reply_text("📩 চ্যানেল থেকে একটি মেসেজ ফরওয়ার্ড করে পাঠাও (বট অবশ্যই অ্যাডমিন হতে হবে)।", reply_markup=back_btn())

def forward_handler(update, ctx):
    msg = update.message
    if not msg.forward_from_chat or msg.forward_from_chat.type != "channel":
        msg.reply_text("❌ এটা চ্যানেল থেকে ফরওয়ার্ড নয়!", reply_markup=back_btn())
        return

    ch = msg.forward_from_chat
    channels = load_json(CHANNEL_FILE)
    if ch.id in [c["id"] for c in channels]:
        msg.reply_text(f"⚠️ {ch.title} আগেই যুক্ত আছে।", reply_markup=main_menu())
        return

    channels.append({"id": ch.id, "title": ch.title or str(ch.id)})
    save_json(CHANNEL_FILE, channels)
    msg.reply_text(f"✅ {ch.title} যুক্ত করা হয়েছে!", reply_markup=main_menu())

# ========================
# 🔹 CREATE POST
# ========================
def create_post_cb(update, ctx):
    q = update.callback_query
    q.answer()
    ctx.user_data.clear()
    q.message.reply_text("📸 এখন ফটো বা ভিডিও পাঠাও (ক্যাপশনসহ দিলে সাথে সেভ হবে)।", reply_markup=back_btn())

def media_handler(update, ctx):
    msg = update.message
    fid, tp = None, None

    if msg.photo:
        fid, tp = msg.photo[-1].file_id, "photo"
    elif msg.video:
        fid, tp = msg.video.file_id, "video"

    if not fid:
        msg.reply_text("❌ শুধু ফটো বা ভিডিও পাঠাও।", reply_markup=back_btn())
        return

    ctx.user_data["fid"] = fid
    ctx.user_data["tp"] = tp

    if msg.caption:
        posts = load_json(POST_FILE)
        posts.append({"id": len(posts)+1, "text": msg.caption, "buttons": "", "fid": fid, "tp": tp})
        save_json(POST_FILE, posts)
        msg.reply_text("✅ ক্যাপশনসহ মিডিয়া সেভ হয়েছে! এখন বাটন যোগ করো।", reply_markup=main_menu())
    else:
        kb = [
            [InlineKeyboardButton("✍️ Add Caption", callback_data="add_cap")],
            [InlineKeyboardButton("⏭️ Skip", callback_data="skip_cap")],
            [InlineKeyboardButton("↩️ Back", callback_data="back")]
        ]
        msg.reply_text("📝 ক্যাপশন দিতে চাও?", reply_markup=InlineKeyboardMarkup(kb))

def caption_choice(update, ctx):
    q = update.callback_query
    q.answer()
    if q.data == "add_cap":
        q.message.reply_text("✍️ ক্যাপশন পাঠাও:", reply_markup=back_btn())
        ctx.user_data["awaiting_cap"] = True
    elif q.data == "skip_cap":
        posts = load_json(POST_FILE)
        posts.append({"id": len(posts)+1, "text": "", "buttons": "", "fid": ctx.user_data["fid"], "tp": ctx.user_data["tp"]})
        save_json(POST_FILE, posts)
        q.message.reply_text("✅ ক্যাপশন ছাড়া সেভ হয়েছে!", reply_markup=main_menu())
        ctx.user_data.clear()

def save_caption(update, ctx):
    if not ctx.user_data.get("awaiting_cap"):
        return
    cap = update.message.text
    posts = load_json(POST_FILE)
    posts.append({"id": len(posts)+1, "text": cap, "buttons": "", "fid": ctx.user_data["fid"], "tp": ctx.user_data["tp"]})
    save_json(POST_FILE, posts)
    update.message.reply_text("✅ ক্যাপশনসহ মিডিয়া সেভ হয়েছে!", reply_markup=main_menu())
    ctx.user_data.clear()

# ========================
# 🔹 SEND POST
# ========================
def send_post_cb(update, ctx):
    q = update.callback_query
    q.answer()
    posts, channels = load_json(POST_FILE), load_json(CHANNEL_FILE)
    if not posts or not channels:
        q.message.reply_text("⚠️ কোনো পোস্ট বা চ্যানেল নেই!", reply_markup=back_btn())
        return

    kb = [[InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"send_{p['id']}")] for p in posts]
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back")])
    q.message.reply_text("📤 কোন পোস্ট পাঠাবো?", reply_markup=InlineKeyboardMarkup(kb))

def send_selected(update, ctx):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[1])
    posts = load_json(POST_FILE)
    post = next((p for p in posts if p["id"] == pid), None)
    if not post:
        q.message.reply_text("❌ পোস্ট পাওয়া যায়নি!", reply_markup=back_btn())
        return

    channels = load_json(CHANNEL_FILE)
    sent = 0
    for ch in channels:
        try:
            markup = parse_buttons(post["buttons"])
            if post["tp"] == "photo":
                ctx.bot.send_photo(ch["id"], post["fid"], caption=post["text"], parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            elif post["tp"] == "video":
                ctx.bot.send_video(ch["id"], post["fid"], caption=post["text"], parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            else:
                ctx.bot.send_message(ch["id"], post["text"], parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            sent += 1
        except Exception as e:
            print("Send error:", e)
    q.message.reply_text(f"✅ পোস্ট {sent} চ্যানেলে পাঠানো হয়েছে।", reply_markup=main_menu())

# ========================
# 🔹 GUIDE + BACK
# ========================
def guide_cb(update, ctx):
    q = update.callback_query
    q.answer()
    q.message.reply_text(
        "🧾 বাটন যোগ করার ফরম্যাট:\n\n"
        "`Title - https://t.me/link1 && Another - https://t.me/link2`\n\n"
        "নতুন লাইনে নতুন রো হবে।",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_btn()
    )

def back_cb(update, ctx):
    q = update.callback_query
    q.answer()
    q.message.reply_text("↩️ মূল মেনুতে ফিরে এসেছো।", reply_markup=main_menu())

# ========================
# 🔹 MAIN BOT FUNCTION
# ========================
def run_bot():
    ensure_files()
    if not TOKEN:
        print("❌ BOT_TOKEN missing!")
        return

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(add_channel_cb, pattern="^add_channel$"))
    dp.add_handler(CallbackQueryHandler(create_post_cb, pattern="^create_post$"))
    dp.add_handler(CallbackQueryHandler(send_post_cb, pattern="^send_post$"))
    dp.add_handler(CallbackQueryHandler(guide_cb, pattern="^guide$"))
    dp.add_handler(CallbackQueryHandler(back_cb, pattern="^back$"))
    dp.add_handler(CallbackQueryHandler(caption_choice, pattern="^(add_cap|skip_cap)$"))
    dp.add_handler(CallbackQueryHandler(send_selected, pattern="^send_"))
    dp.add_handler(MessageHandler(Filters.forwarded, forward_handler))
    dp.add_handler(MessageHandler(Filters.photo | Filters.video, media_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, save_caption))

    print("✅ Render Bot Running ...")
    updater.start_polling()
    updater.idle()

# ========================
# 🔹 FLASK SERVER
# ========================
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Render Bot is Alive!"

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
