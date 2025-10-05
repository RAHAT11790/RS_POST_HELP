import os
import json
import logging
from flask import Flask
from telegram import (
    Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackQueryHandler, CallbackContext
)

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Flask app for Render health checks
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Bot is running successfully!"

@app.route('/health')
def health():
    return "✅ OK", 200

@app.route('/ping')
def ping():
    return "pong", 200

# ----------------- CONFIG -----------------
TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    print("❌ CRITICAL ERROR: BOT_TOKEN environment variable সেট করা নেই!")
    print("👉 Render-এ BOT_TOKEN নামে environment variable সেট করুন")
    print("👉 BotFather থেকে টোকেন নিন: /mybots -> আপনার বট -> API Token")
    exit(1)

CHANNEL_FILE = "channels.json"
POST_FILE = "posts.json"

print(f"✅ Bot Token loaded successfully: {TOKEN[:10]}...")

# ----------------- UTILITIES -----------------
def load_json(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return []

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def ensure_files():
    if not os.path.exists(CHANNEL_FILE):
        save_json(CHANNEL_FILE, [])
    if not os.path.exists(POST_FILE):
        save_json(POST_FILE, [])

# convert a textual button-format into InlineKeyboardMarkup
def parse_buttons_from_text(text):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("&&")]
        row = []
        for p in parts:
            if " - " in p:
                title, action = p.split(" - ", 1)
                title = title.strip()
                action = action.strip()
                if action.startswith(("http://", "https://", "tg://", "https://t.me")):
                    row.append(InlineKeyboardButton(title[:64], url=action))
                else:
                    row.append(InlineKeyboardButton(title[:64], callback_data=action))
            else:
                row.append(InlineKeyboardButton(p[:64], callback_data="noop"))
        if row:
            rows.append(row)
    return InlineKeyboardMarkup(rows) if rows else None

# ----------------- HANDLERS -----------------
def start(update: Update, context: CallbackContext):
    text = (
        "👋 *স্বাগতম — Multi Channel Poster Bot!* \n\n"
        "শুধু `/start` ব্যবহার করো। নিচের বাটনগুলো দিয়ে সব কিছু করা যাবে।\n\n"
        "*Main Menu:*"
    )
    kb = [
        [InlineKeyboardButton("➕ Add channel", callback_data="menu_add_channel"),
         InlineKeyboardButton("📜 Channel list", callback_data="menu_channel_list")],
        [InlineKeyboardButton("✍️ Create post", callback_data="menu_create_post"),
         InlineKeyboardButton("📂 My posts", callback_data="menu_my_posts")],
        [InlineKeyboardButton("📤 Send post", callback_data="menu_send_post"),
         InlineKeyboardButton("🌐 All Channels (Send)", callback_data="menu_send_all")],
        [InlineKeyboardButton("🧾 Multipost", callback_data="menu_multipost"),
         InlineKeyboardButton("✏️ Edit post", callback_data="menu_edit_post")],
        [InlineKeyboardButton("🗑 Delete", callback_data="menu_delete"),
         InlineKeyboardButton("📘 Button Guide", callback_data="menu_guide")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings")]
    ]
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

# ---------- Add Channel ----------
def menu_add_channel_cb(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    query.message.reply_text(
        "📩 চ্যানেল অ্যাড করতে, *চ্যানেল থেকে একটি মেসেজ ফরওয়ার্ড* করে এখানে পাঠাও।\n\n"
        "⚠️ নিশ্চিত করো বটটি সেই চ্যানেলে *admin* আছে।",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['expecting_forward_for_add'] = True

def forward_handler(update: Update, context: CallbackContext):
    msg = update.message
    if not msg.forward_from_chat:
        update.message.reply_text("❌ এটি চ্যানেল থেকে ফরওয়ার্ড করা ম্যাসেজ নয়। দয়া করে চ্যানেল থেকে ফরওয়ার্ড করো।")
        return

    chat = msg.forward_from_chat
    if chat.type != 'channel':
        update.message.reply_text("❌ ফরওয়ার্ড করা মেসেজটি একটি চ্যানেলের নয়। চ্যানেল থেকে ফরওয়ার্ড করো।")
        return

    channels = load_json(CHANNEL_FILE)
    existing_ids = [c['id'] for c in channels]
    if chat.id in existing_ids:
        update.message.reply_text(f"⚠️ চ্যানেল *{chat.title}* আগে থেকেই যুক্ত আছে।", parse_mode=ParseMode.MARKDOWN)
        return

    channels.append({'id': chat.id, 'title': chat.title or str(chat.id)})
    save_json(CHANNEL_FILE, channels)
    update.message.reply_text(f"✅ চ্যানেল *{chat.title}* সফলভাবে যুক্ত হয়েছে!", parse_mode=ParseMode.MARKDOWN)

# ---------- Channel List ----------
def menu_channel_list_cb(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    channels = load_json(CHANNEL_FILE)
    if not channels:
        query.message.reply_text("📭 এখনো কোনো চ্যানেল নেই। Add channel দিয়ে চ্যানেল যোগ করো।")
        return

    kb = []
    for ch in channels:
        kb.append([InlineKeyboardButton(ch['title'][:40], callback_data=f"view_channel_{ch['id']}"),
                   InlineKeyboardButton("❌ Remove", callback_data=f"remove_channel_{ch['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    query.message.reply_text("📜 আপনার চ্যানেলগুলো:", reply_markup=InlineKeyboardMarkup(kb))

def view_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    payload = q.data.split("_", 2)
    if len(payload) < 3:
        q.message.reply_text("Invalid")
        return
    ch_id = int(payload[2])
    channels = load_json(CHANNEL_FILE)
    ch = next((c for c in channels if c['id'] == ch_id), None)
    if not ch:
        q.message.reply_text("Channel not found.")
        return
    q.message.reply_text(f"📣 Channel: *{ch['title']}*\nID: `{ch['id']}`", parse_mode=ParseMode.MARKDOWN)

def remove_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer("Removing...")
    ch_id = int(q.data.split("_", 2)[2])
    channels = load_json(CHANNEL_FILE)
    channels = [c for c in channels if c['id'] != ch_id]
    save_json(CHANNEL_FILE, channels)
    q.message.reply_text("✅ চ্যানেল মুছে দেয়া হয়েছে।")

# ---------- Create Post ----------
def menu_create_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    q.message.reply_text(
        "✍️ এখন পোস্টের *টেক্সট* পাঠাও।\n"
        "যদি বাটন যোগ করতে চাও, নিচের ফরম্যাট ব্যবহার করো (Guide দেখার জন্য বাটন আছে):\n\n"
        "উদাহরণ:\n"
        "⎙ Watch & Download ⎙\n"
        "𓆩FANDUB𓆪 - https://t.me/fandub01 && 𓆩💬GROUP𓆪 - https://t.me/hindianime03\n\n"
        "একই পোস্টে একাধিক লাইন হলে প্রতিটি লাইন নতুন বাটন রো হিসেবে গন্য হবে।",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['creating_post'] = True

def save_post_text(update: Update, context: CallbackContext):
    if not context.user_data.get('creating_post'):
        return

    text = update.message.text or ""
    posts = load_json(POST_FILE)
    lines = text.splitlines()
    btn_lines = []
    main_lines = []
    started_buttons = False
    for line in lines:
        if " - " in line and (("http" in line) or ("t.me" in line) or "&&" in line or "popup:" in line or "alert:" in line or "share:" in line):
            started_buttons = True
            btn_lines.append(line)
        else:
            if started_buttons:
                btn_lines.append(line)
            else:
                main_lines.append(line)
    main_text = "\n".join(main_lines).strip() if main_lines else text.strip()
    btn_text = "\n".join(btn_lines).strip() if btn_lines else ""

    post_obj = {
        "id": None,
        "text": main_text or "(empty)",
        "buttons_raw": btn_text
    }
    posts.append(post_obj)
    for i, p in enumerate(posts):
        p['id'] = i + 1
    save_json(POST_FILE, posts)
    update.message.reply_text("✅ পোস্ট সংরক্ষণ করা হয়েছে! (My posts দেখো)")
    context.user_data.pop('creating_post', None)

# ---------- My Posts ----------
def menu_my_posts_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("🗂 কোনো পোস্ট পাওয়া যায়নি। Create post দিয়ে পোস্ট যোগ করো।")
        return
    kb = []
    for p in posts:
        kb.append([InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"view_post_{p['id']}"),
                   InlineKeyboardButton("🗑", callback_data=f"del_post_{p['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
    q.message.reply_text("🗂 আপনার পোস্টগুলো:", reply_markup=InlineKeyboardMarkup(kb))

def view_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[-1])
    posts = load_json(POST_FILE)
    p = next((x for x in posts if x['id'] == pid), None)
    if not p:
        q.message.reply_text("Post not found.")
        return
    text = f"*Post {p['id']}*\n\n{p['text']}"
    markup = parse_buttons_from_text(p['buttons_raw']) if p.get('buttons_raw') else None
    q.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

def del_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[-1])
    posts = load_json(POST_FILE)
    posts = [p for p in posts if p['id'] != pid]
    for i, p in enumerate(posts):
        p['id'] = i + 1
    save_json(POST_FILE, posts)
    q.message.reply_text("✅ পোস্ট মুছে দেয়া হয়েছে।")

# ---------- Send Post ----------
def menu_send_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    channels = load_json(CHANNEL_FILE)
    if not posts:
        q.message.reply_text("❗ কোনো পোস্ট নেই। আগে Create post দিয়ে পোস্ট যোগ করো।")
        return
    if not channels:
        q.message.reply_text("❗ কোনো চ্যানেল যোগ করা নেই। Add channel দিয়ে চ্যানেল যোগ করো।")
        return
    kb = []
    for p in posts:
        kb.append([InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"choose_send_post_{p['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
    q.message.reply_text("📝 কোন পোস্ট পাঠাতে চাও?", reply_markup=InlineKeyboardMarkup(kb))

def choose_send_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[-1])
    context.user_data['send_post_id'] = pid
    channels = load_json(CHANNEL_FILE)
    kb = []
    for ch in channels:
        kb.append([InlineKeyboardButton(ch['title'][:30], callback_data=f"send_to_channel_{ch['id']}")])
    kb.append([InlineKeyboardButton("🌐 Send to All Channels", callback_data="send_to_all")])
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
    q.message.reply_text("📤 কোন চ্যানেলে পাঠাতে চাও?", reply_markup=InlineKeyboardMarkup(kb))

def send_to_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    data = q.data
    posts = load_json(POST_FILE)
    pid = context.user_data.get('send_post_id')
    post = next((x for x in posts if x['id'] == pid), None)
    if not post:
        q.message.reply_text("❌ পোস্ট পাওয়া যায়নি।")
        return

    if data == "send_to_all":
        channels = load_json(CHANNEL_FILE)
        sent = 0
        for ch in channels:
            try:
                markup = parse_buttons_from_text(post.get('buttons_raw', ''))
                bot = context.bot
                bot.send_message(chat_id=ch['id'], text=post['text'], parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
                sent += 1
            except Exception as e:
                print("Send error:", e)
        q.message.reply_text(f"✅ মোট {sent} চ্যানেলে পোস্ট পাঠানো হয়েছে।")
    else:
        ch_id = int(data.split("_")[-1])
        try:
            markup = parse_buttons_from_text(post.get('buttons_raw', ''))
            bot = context.bot
            bot.send_message(chat_id=ch_id, text=post['text'], parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            q.message.reply_text("✅ পোস্ট পাঠানো হয়েছে!")
        except Exception as e:
            print("Send error single:", e)
            q.message.reply_text("❌ পোস্ট পাঠাতে সমস্যা হয়েছে।")

    context.user_data.pop('send_post_id', None)

# ---------- Multipost ----------
def menu_multipost_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    q.message.reply_text(
        "🧾 Multipost: একসাথে একাধিক পোস্ট যোগ করতে, প্রতিটি পোস্ট আলাদা করতে তিনটি ড্যাশ (---) ব্যবহার করো।\n\n"
        "উদাহরণ:\nPost text 1\nbutton1 - https://t.me/a\n---\nPost text 2\nbutton2 - https://t.me/b && button3 - https://t.me/c"
    )
    context.user_data['creating_multipost'] = True

def save_multiposts_text(update: Update, context: CallbackContext):
    if not context.user_data.get('creating_multipost'):
        return
    raw = update.message.text or ""
    parts = [p.strip() for p in raw.split("---") if p.strip()]
    posts = load_json(POST_FILE)
    for part in parts:
        lines = part.splitlines()
        btn_lines = []
        main_lines = []
        started_buttons = False
        for line in lines:
            if " - " in line and (("http" in line) or ("t.me" in line) or "&&" in line or "popup:" in line or "alert:" in line or "share:" in line):
                started_buttons = True
                btn_lines.append(line)
            else:
                if started_buttons:
                    btn_lines.append(line)
                else:
                    main_lines.append(line)
        main_text = "\n".join(main_lines).strip()
        btn_text = "\n".join(btn_lines).strip()
        posts.append({"id": None, "text": main_text or "(empty)", "buttons_raw": btn_text})
    for i, p in enumerate(posts):
        p['id'] = i + 1
    save_json(POST_FILE, posts)
    update.message.reply_text(f"✅ মোট {len(parts)}টি পোস্ট যোগ করা হয়েছে!")
    context.user_data.pop('creating_multipost', None)

# ---------- Edit Post ----------
def menu_edit_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("❗ কোনো পোস্ট নেই।")
        return
    kb = []
    for p in posts:
        kb.append([InlineKeyboardButton(f"✏️ Edit {p['id']}", callback_data=f"edit_post_{p['id']}")])
    q.message.reply_text("✏️ কোন পোস্ট এডিট করতে চাও?", reply_markup=InlineKeyboardMarkup(kb))

def choose_edit_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[-1])
    context.user_data['editing_post'] = pid
    q.message.reply_text("✏️ নতুন টেক্সট পাঠাও (বাটন যোগ করতে চাইলে guide ফরম্যাট অনুসরণ করো)।")

def save_edited_post(update: Update, context: CallbackContext):
    if 'editing_post' not in context.user_data:
        return
    pid = context.user_data['editing_post']
    text = update.message.text or ""
    posts = load_json(POST_FILE)
    p = next((x for x in posts if x['id'] == pid), None)
    if not p:
        update.message.reply_text("❌ পোস্ট পাওয়া যায়নি।")
        context.user_data.pop('editing_post', None)
        return
    lines = text.splitlines()
    btn_lines = []
    main_lines = []
    started_buttons = False
    for line in lines:
        if " - " in line and (("http" in line) or ("t.me" in line) or "&&" in line or "popup:" in line or "alert:" in line or "share:" in line):
            started_buttons = True
            btn_lines.append(line)
        else:
            if started_buttons:
                btn_lines.append(line)
            else:
                main_lines.append(line)
    p['text'] = "\n".join(main_lines).strip() or "(empty)"
    p['buttons_raw'] = "\n".join(btn_lines).strip()
    save_json(POST_FILE, posts)
    update.message.reply_text("✅ পোস্ট আপডেট হয়েছে।")
    context.user_data.pop('editing_post', None)

# ---------- Delete Menu ----------
def menu_delete_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    kb = [
        [InlineKeyboardButton("🗑 Delete Post", callback_data="start_delete_post"),
         InlineKeyboardButton("🗑 Remove Channel", callback_data="start_delete_channel")],
        [InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")]
    ]
    q.message.reply_text("Delete options:", reply_markup=InlineKeyboardMarkup(kb))

def start_delete_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("No posts to delete.")
        return
    kb = [[InlineKeyboardButton(f"Del {p['id']}", callback_data=f"del_post_{p['id']}")] for p in posts]
    q.message.reply_text("Choose post to delete:", reply_markup=InlineKeyboardMarkup(kb))

def start_delete_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    channels = load_json(CHANNEL_FILE)
    if not channels:
        q.message.reply_text("No channels to remove.")
        return
    kb = [[InlineKeyboardButton(c['title'][:30], callback_data=f"remove_channel_{c['id']}")] for c in channels]
    q.message.reply_text("Choose channel to remove:", reply_markup=InlineKeyboardMarkup(kb))

# ---------- Guide ----------
def menu_guide_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    text = (
        "*Button Format Guide*\n\n"
        "• Single button:\n"
        "`Button text - https://t.me/example`\n\n"
        "• Multiple buttons in same line:\n"
        "`Button 1 - https://t.me/a && Button 2 - https://t.me/b`\n\n"
        "• Multiple rows: write buttons on new lines.\n\n"
        "• Popup/Alert buttons (will show alert on click):\n"
        "`Button - alert: This is message`\n\n"
        "• Share button:\n"
        "`Button - share: Text to share`\n\n"
        "Example full post:\n"
        "Title line\n"
        "Button A - https://t.me/a && Button B - https://t.me/b\n"
    )
    q.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ---------- Settings ----------
def menu_settings_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    q.message.reply_text("⚙️ Settings: (nothing to set yet)")

# ---------- Back to Menu ----------
def back_to_menu_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    start(q, context)

# ---------- Generic Callback ----------
def generic_callback_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    data = q.data or ""
    q.answer()
    if data.startswith("popup:") or data.startswith("alert:"):
        message = data.split(":", 1)[1].strip()
        try:
            q.answer(text=message, show_alert=True)
        except:
            q.message.reply_text(message)
    elif data == "noop":
        q.answer(text="🔘 বাটন ক্লিক হয়েছে (কোনো কার্য নেই)।", show_alert=True)
    else:
        q.answer(text="🔘 বাটন ক্লিক হয়েছে!", show_alert=True)

# ----------------- MAIN -----------------
def main():
    ensure_files()
    
    print("🤖 Starting Telegram Bot...")
    print(f"📁 Channel file: {CHANNEL_FILE}")
    print(f"📁 Post file: {POST_FILE}")
    
    try:
        updater = Updater(TOKEN, use_context=True)
        dp = updater.dispatcher

        # commands
        dp.add_handler(CommandHandler("start", start))

        # callbacks - menu routing
        dp.add_handler(CallbackQueryHandler(menu_add_channel_cb, pattern="^menu_add_channel$"))
        dp.add_handler(CallbackQueryHandler(menu_channel_list_cb, pattern="^menu_channel_list$"))
        dp.add_handler(CallbackQueryHandler(menu_create_post_cb, pattern="^menu_create_post$"))
        dp.add_handler(CallbackQueryHandler(menu_my_posts_cb, pattern="^menu_my_posts$"))
        dp.add_handler(CallbackQueryHandler(menu_send_post_cb, pattern="^menu_send_post$"))
        dp.add_handler(CallbackQueryHandler(menu_multipost_cb, pattern="^menu_multipost$"))
        dp.add_handler(CallbackQueryHandler(menu_edit_post_cb, pattern="^menu_edit_post$"))
        dp.add_handler(CallbackQueryHandler(menu_delete_cb, pattern="^menu_delete$"))
        dp.add_handler(CallbackQueryHandler(menu_guide_cb, pattern="^menu_guide$"))
        dp.add_handler(CallbackQueryHandler(menu_settings_cb, pattern="^menu_settings$"))
        dp.add_handler(CallbackQueryHandler(back_to_menu_cb, pattern="^back_to_menu$"))

        # dynamic callbacks for view/remove/send etc.
        dp.add_handler(CallbackQueryHandler(view_channel_cb, pattern=r"^view_channel_"))
        dp.add_handler(CallbackQueryHandler(remove_channel_cb, pattern=r"^remove_channel_"))
        dp.add_handler(CallbackQueryHandler(view_post_cb, pattern=r"^view_post_"))
        dp.add_handler(CallbackQueryHandler(del_post_cb, pattern=r"^del_post_"))
        dp.add_handler(CallbackQueryHandler(choose_send_post_cb, pattern=r"^choose_send_post_"))
        dp.add_handler(CallbackQueryHandler(send_to_channel_cb, pattern=r"^send_to_channel_"))
        dp.add_handler(CallbackQueryHandler(send_to_channel_cb, pattern=r"^send_to_all$"))
        dp.add_handler(CallbackQueryHandler(start_delete_post_cb, pattern=r"^start_delete_post$"))
        dp.add_handler(CallbackQueryHandler(start_delete_channel_cb, pattern=r"^start_delete_channel$"))
        dp.add_handler(CallbackQueryHandler(choose_edit_post_cb, pattern=r"^edit_post_"))

        # generic for popup/alert/noop
        dp.add_handler(CallbackQueryHandler(generic_callback_cb, pattern=r"^(popup:|alert:|noop)"))

        # menu_send_all handler
        def menu_send_all_cb_func(update: Update, context: CallbackContext):
            q = update.callback_query
            q.answer()
            posts = load_json(POST_FILE)
            channels = load_json(CHANNEL_FILE)
            if not posts or not channels:
                q.message.reply_text("Make sure you have posts and channels added.")
                return
            kb = []
            for p in posts:
                kb.append([InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"choose_send_post_{p['id']}")])
            kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
            q.message.reply_text("Choose a post to send to ALL channels:", reply_markup=InlineKeyboardMarkup(kb))
        
        dp.add_handler(CallbackQueryHandler(menu_send_all_cb_func, pattern="^menu_send_all$"))

        # message handlers
        dp.add_handler(MessageHandler(Filters.forwarded & Filters.chat_type.private, forward_handler))
        dp.add_handler(MessageHandler(Filters.text & Filters.chat_type.private, save_post_text))
        dp.add_handler(MessageHandler(Filters.text & Filters.chat_type.private, save_multiposts_text))
        dp.add_handler(MessageHandler(Filters.text & Filters.chat_type.private, save_edited_post))

        print("✅ Bot started successfully!")
        updater.start_polling()
        updater.idle()
        
    except Exception as e:
        print(f"❌ Bot startup failed: {e}")
        exit(1)

if __name__ == "__main__":
    main()