# help_bot.py
# Render-ready single-file Telegram MultiPost Bot
# - Keeps all original features from your bot.py
# - Adds smart media flow: save media, if caption missing -> Add Caption / Skip
# - After media saved, prompts to Add Buttons (or use Button Guide)
# - Back buttons on menus everywhere
# - Flask keep-alive (works on Render)
# Requirements: python-telegram-bot==13.15, Flask==3.0.3
# Recommended: Set Render env var PYTHON_VERSION = 3.11.9 to avoid imghdr issues on Python 3.13

import os
import json
import logging
import threading
from flask import Flask
from telegram import (
    Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackQueryHandler, CallbackContext
)

# -----------------------
# Logging
# -----------------------
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# -----------------------
# Config / Files
# -----------------------
TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_FILE = "channels.json"
POST_FILE = "posts.json"

# -----------------------
# Helpers: JSON file IO
# -----------------------
def load_json(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return []

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def ensure_files():
    if not os.path.exists(CHANNEL_FILE):
        save_json(CHANNEL_FILE, [])
    if not os.path.exists(POST_FILE):
        save_json(POST_FILE, [])

# -----------------------
# Button parser (keeps original format)
# Example lines:
#   Button text - https://t.me/link && Button 2 - https://t.me/link2
# Multiple lines = multiple rows
# -----------------------
def parse_buttons_from_text(text):
    if not text:
        return None
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
                # treat as URL if looks like one
                if action.startswith(("http://", "https://", "tg://", "https://t.me")):
                    row.append(InlineKeyboardButton(title[:64], url=action))
                else:
                    # fallback to callback data (for popup/alert patterns maybe)
                    row.append(InlineKeyboardButton(title[:64], callback_data=action))
            else:
                row.append(InlineKeyboardButton(p[:64], callback_data="noop"))
        if row:
            rows.append(row)
    return InlineKeyboardMarkup(rows) if rows else None

# -----------------------
# UI keyboards (main/back)
# -----------------------
def main_menu_kb():
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
         InlineKeyboardButton("📘 Button Guide", callback_data="menu_guide")]
    ]
    return InlineKeyboardMarkup(kb)

def back_to_menu_kb(text="↩️ Back to Menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data="back_to_menu")]])

# -----------------------
# /start command
# -----------------------
def start(update: Update, context: CallbackContext):
    txt = (
        "👋 *স্বাগতম — Multi Channel Poster Bot!* \n\n"
        "শুধু `/start` ব্যবহার করো। নিচের বাটনগুলো দিয়ে সব কিছু করা যাবে।\n\n"
        "📘 বাটন গাইড দেখতে ‘Button Guide’ বাটনে চাপ দাও।"
    )
    update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())

# -----------------------
# Add Channel flow (forwarded message from channel)
# -----------------------
def menu_add_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    q.message.reply_text(
        "📩 চ্যানেল অ্যাড করতে, *চ্যানেল থেকে একটি মেসেজ ফরওয়ার্ড* করে এখানে পাঠাও।\n\n"
        "⚠️ নিশ্চিত করো বটটি সেই চ্যানেলে *admin* আছে।",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_to_menu_kb()
    )
    context.user_data['expecting_forward_for_add'] = True

def forward_handler(update: Update, context: CallbackContext):
    msg = update.message
    if not msg.forward_from_chat:
        update.message.reply_text("❌ এটি চ্যানেল থেকে ফরওয়ার্ড করা মেসেজ নয়। দয়া করে চ্যানেল থেকে ফরওয়ার্ড করো।", reply_markup=back_to_menu_kb())
        return

    chat = msg.forward_from_chat
    if chat.type != 'channel':
        update.message.reply_text("❌ ফরওয়ার্ড করা মেসেজটি একটি চ্যানেলের নয়।", reply_markup=back_to_menu_kb())
        return

    channels = load_json(CHANNEL_FILE)
    existing_ids = [c['id'] for c in channels]
    if chat.id in existing_ids:
        update.message.reply_text(f"⚠️ চ্যানেল *{chat.title}* আগে থেকেই যুক্ত আছে।", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())
        context.user_data.pop('expecting_forward_for_add', None)
        return

    channels.append({'id': chat.id, 'title': chat.title or str(chat.id)})
    save_json(CHANNEL_FILE, channels)
    update.message.reply_text(f"✅ চ্যানেল *{chat.title}* সফলভাবে যুক্ত হয়েছে!", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())
    context.user_data.pop('expecting_forward_for_add', None)

# -----------------------
# Channel list & remove/view
# -----------------------
def menu_channel_list_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    channels = load_json(CHANNEL_FILE)
    if not channels:
        q.message.reply_text("📭 এখনো কোনো চ্যানেল নেই। Add channel দিয়ে চ্যানেল যোগ করো।", reply_markup=back_to_menu_kb())
        return

    kb = []
    for ch in channels:
        kb.append([InlineKeyboardButton(ch['title'][:40], callback_data=f"view_channel_{ch['id']}"),
                   InlineKeyboardButton("❌ Remove", callback_data=f"remove_channel_{ch['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    q.message.reply_text("📜 আপনার চ্যানেলগুলো:", reply_markup=InlineKeyboardMarkup(kb))

def view_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    parts = q.data.split("_", 2)
    if len(parts) < 3:
        q.message.reply_text("Invalid")
        return
    ch_id = int(parts[2])
    channels = load_json(CHANNEL_FILE)
    ch = next((c for c in channels if c['id'] == ch_id), None)
    if not ch:
        q.message.reply_text("Channel not found.", reply_markup=back_to_menu_kb())
        return
    q.message.reply_text(f"📣 Channel: *{ch['title']}*\nID: `{ch['id']}`", parse_mode=ParseMode.MARKDOWN, reply_markup=back_to_menu_kb())

def remove_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer("Removing...")
    try:
        ch_id = int(q.data.split("_", 2)[2])
    except:
        q.message.reply_text("Invalid")
        return
    channels = load_json(CHANNEL_FILE)
    channels = [c for c in channels if c['id'] != ch_id]
    save_json(CHANNEL_FILE, channels)
    q.message.reply_text("✅ চ্যানেল মুছে দেয়া হয়েছে।", reply_markup=main_menu_kb())

# -----------------------
# Create post flow (text or media-first)
# - If media has caption -> saved immediately
# - If media has no caption -> ask Add Caption / Skip
# - After saving media (or text), bot will prompt to add buttons
# -----------------------
def menu_create_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    context.user_data.clear()
    context.user_data['creating_post'] = True
    q.message.reply_text(
        "📝 পোস্ট তৈরি শুরু হয়েছে।\n\n"
        "আপনি চাইলে *প্রথমে মিডিয়া* (ছবি/ভিডিও) পাঠাতে পারো — মিডিয়া পাঠালে বট সেটি সেভ করবে এবং পরে আপনি বাটন যোগ করতে পারবেন।\n"
        "অথবা সরাসরি টেক্সট পাঠালে সেটাও পোস্ট হিসেবে সেভ হবে।\n\n"
        "📎 মিডিয়া পাঠালে বট ক্যাপশন চেক করবে — যদি না থাকে তাহলে আপনি *Add Caption* বা *Skip* করে এগোতে পারবেন।",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_to_menu_kb()
    )

# Save text when creating a text-only post OR when awaiting caption OR when awaiting buttons, logic unified
def save_text_handler(update: Update, context: CallbackContext):
    user = context.user_data

    # 1) If user is adding buttons to an existing post (awaiting_buttons_for_post_id)
    if user.get('awaiting_buttons_for_post_id'):
        post_id = user.get('awaiting_buttons_for_post_id')
        buttons_raw = update.message.text or ""
        posts = load_json(POST_FILE)
        p = next((x for x in posts if x['id'] == post_id), None)
        if not p:
            update.message.reply_text("❌ পোস্ট পাওয়া যায়নি।", reply_markup=main_menu_kb())
            user.pop('awaiting_buttons_for_post_id', None)
            return
        p['buttons_raw'] = buttons_raw
        save_json(POST_FILE, posts)
        update.message.reply_text("✅ বাটন সংরক্ষণ হয়েছে! আপনি এখন পোস্ট পাঠাতে পারেন।", reply_markup=main_menu_kb())
        user.pop('awaiting_buttons_for_post_id', None)
        return

    # 2) If user is awaiting caption text for a media they chose Add Caption for
    if user.get('awaiting_caption_text'):
        caption = update.message.text or ""
        fid = user.get('pending_file_id')
        mtype = user.get('pending_type')
        posts = load_json(POST_FILE)
        posts.append({
            "id": len(posts) + 1,
            "text": caption,
            "buttons_raw": "",
            "media_id": fid,
            "media_type": mtype
        })
        save_json(POST_FILE, posts)
        update.message.reply_text("✅ ক্যাপশনসহ মিডিয়া সেভ হয়েছে! এখন বাটন যোগ করতে পারো।", reply_markup=main_menu_kb())
        user.pop('awaiting_caption_text', None)
        user.pop('pending_file_id', None)
        user.pop('pending_type', None)
        return

    # 3) Multipost saving (original functionality kept)
    if user.get('creating_multipost'):
        text = update.message.text or ""
        raw = text
        parts = [p.strip() for p in raw.split("---") if p.strip()]
        posts = load_json(POST_FILE)
        for part in parts:
            lines = part.splitlines()
            btn_lines = []
            main_lines = []
            started_buttons = False
            for line in lines:
                if " - " in line and (("http" in line) or ("t.me" in line) or "&&" in line):
                    started_buttons = True
                    btn_lines.append(line)
                else:
                    if started_buttons:
                        btn_lines.append(line)
                    else:
                        main_lines.append(line)
            main_text = "\n".join(main_lines).strip()
            btn_text = "\n".join(btn_lines).strip()
            posts.append({"id": None, "text": main_text or "(empty)", "buttons_raw": btn_text, "media_id": None, "media_type": None})
        for i, p in enumerate(posts):
            p['id'] = i + 1
        save_json(POST_FILE, posts)
        update.message.reply_text(f"✅ মোট {len(parts)}টি পোস্ট যোগ করা হয়েছে!", reply_markup=main_menu_kb())
        user.pop('creating_multipost', None)
        return

    # 4) Editing existing post (original edit flow kept)
    if user.get('editing_post'):
        pid = user.get('editing_post')
        text = update.message.text or ""
        posts = load_json(POST_FILE)
        p = next((x for x in posts if x['id'] == pid), None)
        if not p:
            update.message.reply_text("❌ পোস্ট পাওয়া যায়নি।", reply_markup=main_menu_kb())
            user.pop('editing_post', None)
            return
        # heuristic parse: split lines into main text and button lines
        lines = text.splitlines()
        btn_lines = []
        main_lines = []
        started_buttons = False
        for line in lines:
            if " - " in line and (("http" in line) or ("t.me" in line) or "&&" in line):
                started_buttons = True
                btn_lines.append(line)
            else:
                if started_buttons:
                    btn_lines.append(line)
                else:
                    main_lines.append(line)
        if main_lines:
            p['text'] = "\n".join(main_lines).strip()
        if btn_lines:
            p['buttons_raw'] = "\n".join(btn_lines).strip()
        save_json(POST_FILE, posts)
        update.message.reply_text("✅ পোস্ট আপডেট হয়েছে!", reply_markup=main_menu_kb())
        user.pop('editing_post', None)
        return

    # 5) Regular "create_post" text handling (if creating_post True)
    if user.get('creating_post'):
        text = update.message.text or ""
        # original save_post_text parsing (keeps original functionality)
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
        main_text = "\n".join(main_lines).strip()
        btn_text = "\n".join(btn_lines).strip()
        posts.append({"id": len(posts) + 1, "text": main_text, "buttons_raw": btn_text, "media_id": None, "media_type": None})
        save_json(POST_FILE, posts)
        update.message.reply_text("✅ পোস্ট সংরক্ষণ করা হয়েছে!", reply_markup=main_menu_kb())
        user.pop('creating_post', None)
        return

    # otherwise ignore or not relevant
    return

# -----------------------
# Media handler (photo/video/animation)
# - If caption present -> save immediately
# - If caption missing -> ask Add Caption / Skip
# After save, bot prompts to add buttons
# -----------------------
def media_handler(update: Update, context: CallbackContext):
    msg = update.message
    fid = None
    mtype = None
    if msg.photo:
        fid = msg.photo[-1].file_id
        mtype = "photo"
    elif msg.video:
        fid = msg.video.file_id
        mtype = "video"
    elif msg.animation:
        fid = msg.animation.file_id
        mtype = "animation"

    if not fid:
        msg.reply_text("❌ শুধু ছবি/ভিডিও/GIF পাঠাও।", reply_markup=back_to_menu_kb())
        return

    # If caption already present -> save immediately and ask to add buttons
    if msg.caption:
        posts = load_json(POST_FILE)
        posts.append({
            "id": len(posts) + 1,
            "text": msg.caption,
            "buttons_raw": "",
            "media_id": fid,
            "media_type": mtype
        })
        save_json(POST_FILE, posts)
        # Prompt to add buttons for this newly saved post
        new_id = len(posts)
        kb = [
            [InlineKeyboardButton("➕ Add Buttons", callback_data=f"add_buttons_{new_id}")],
            [InlineKeyboardButton("📘 Button Guide", callback_data="menu_guide")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        msg.reply_text("✅ মিডিয়া ও ক্যাপশন সেভ হয়েছে! এখন চাইলে বাটন যোগ করো:", reply_markup=InlineKeyboardMarkup(kb))
        return

    # If no caption -> ask whether to add caption or skip
    context.user_data['pending_file_id'] = fid
    context.user_data['pending_type'] = mtype
    kb = [
        [InlineKeyboardButton("✍️ Add Caption", callback_data="add_caption")],
        [InlineKeyboardButton("⏭️ Skip (no caption)", callback_data="skip_caption")],
        [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
    ]
    msg.reply_text("📝 আপনি কি ক্যাপশন যোগ করতে চান?", reply_markup=InlineKeyboardMarkup(kb))

# Caption choice callback (from media flow)
def caption_choice_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    data = q.data
    if data == "add_caption":
        q.message.reply_text("✍️ এখন ক্যাপশন লিখে পাঠান:", reply_markup=back_to_menu_kb())
        context.user_data['awaiting_caption_text'] = True
    elif data == "skip_caption":
        fid = context.user_data.get('pending_file_id')
        mtype = context.user_data.get('pending_type')
        posts = load_json(POST_FILE)
        posts.append({
            "id": len(posts) + 1,
            "text": "",
            "buttons_raw": "",
            "media_id": fid,
            "media_type": mtype
        })
        save_json(POST_FILE, posts)
        new_id = len(posts)
        kb = [
            [InlineKeyboardButton("➕ Add Buttons", callback_data=f"add_buttons_{new_id}")],
            [InlineKeyboardButton("📤 Send Post", callback_data=f"send_post_{new_id}")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        q.message.reply_text("✅ মিডিয়া (ক্যাপশন ছাড়া) সেভ করা হয়েছে! এখন চাইলে বাটন যোগ করো বা সরাসরি পাঠাও:", reply_markup=InlineKeyboardMarkup(kb))
        context.user_data.pop('pending_file_id', None)
        context.user_data.pop('pending_type', None)
    else:
        q.message.reply_text("❌ অঅ-অজানা অপশন", reply_markup=main_menu_kb())

# Add Buttons callback: set awaiting_buttons_for_post_id and instruct user to send button lines
def add_buttons_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    # data format: add_buttons_<postid>
    parts = q.data.split("_")
    if len(parts) >= 3:
        try:
            pid = int(parts[2])
        except:
            pid = None
    else:
        pid = None
    if not pid:
        q.message.reply_text("❌ পোস্ট আইডি পাওয়া যায়নি।", reply_markup=main_menu_kb())
        return
    # set awaiting flag
    context.user_data['awaiting_buttons_for_post_id'] = pid
    q.message.reply_text(
        "✍️ এখন বাটন লাইন পাঠাও (উদাহরণ):\n`⎙ WATCH & DOWNLOAD ⎙ - https://t.me/fandub01 && 💬 GROUP - https://t.me/hindianime03`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_to_menu_kb()
    )

# -----------------------
# My posts / view / delete / edit flows
# (keeping original behavior; added media preview support)
# -----------------------
def menu_my_posts_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("📭 কোনো পোস্ট নেই। Create post দিয়ে পোস্ট যোগ করো।", reply_markup=back_to_menu_kb())
        return
    kb = []
    for p in posts:
        kb.append([InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"view_post_{p['id']}"),
                   InlineKeyboardButton("🗑 Delete", callback_data=f"del_post_{p['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    q.message.reply_text("🗂 আপনার পোস্টগুলো:", reply_markup=InlineKeyboardMarkup(kb))

def view_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[-1])
    posts = load_json(POST_FILE)
    p = next((x for x in posts if x['id'] == pid), None)
    if not p:
        q.message.reply_text("Post not found.", reply_markup=back_to_menu_kb())
        return
    text = f"*Post {p['id']}*\n\n{p.get('text','')}"
    markup = parse_buttons_from_text(p.get('buttons_raw',''))
    try:
        if p.get('media_type') == "photo":
            q.message.reply_photo(photo=p['media_id'], caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
        elif p.get('media_type') == "video":
            q.message.reply_video(video=p['media_id'], caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
        elif p.get('media_type') == "animation":
            q.message.reply_animation(animation=p['media_id'], caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
        else:
            q.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
    except Exception as e:
        # fallback to text
        q.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

def del_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[-1])
    posts = load_json(POST_FILE)
    posts = [p for p in posts if p['id'] != pid]
    # reassign ids to keep sequential
    for i,p in enumerate(posts):
        p['id'] = i+1
    save_json(POST_FILE, posts)
    q.message.reply_text("✅ পোস্ট মুছে দেয়া হয়েছে।", reply_markup=main_menu_kb())

# Edit post flow (user chooses a post then sends new text/btn-lines)
def menu_edit_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("❗ কোনো পোস্ট নেই।", reply_markup=back_to_menu_kb())
        return
    kb = [[InlineKeyboardButton(f"✏️ Edit {p['id']}", callback_data=f"edit_post_{p['id']}")] for p in posts]
    kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    q.message.reply_text("✏️ কোন পোস্ট এডিট করতে চাও?", reply_markup=InlineKeyboardMarkup(kb))

def choose_edit_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[-1])
    context.user_data['editing_post'] = pid
    q.message.reply_text("✏️ নতুন টেক্সট বা বাটন লাইন পাঠাও (বাটন ফরম্যাট দেখতে Guide চাপো).", reply_markup=back_to_menu_kb())

# -----------------------
# Multipost (original supported multi-line with ---)
# -----------------------
def menu_multipost_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    q.message.reply_text(
        "🧾 Multipost: একসাথে একাধিক পোস্ট যুক্ত করতে প্রতিটি পোস্ট আলাদা করতে `---` ব্যবহার করো।\n\n"
        "উদাহরণ:\nPost text 1\nbutton - https://t.me/a\n---\nPost text 2\nbutton - https://t.me/b && button2 - https://t.me/c",
        reply_markup=back_to_menu_kb()
    )
    context.user_data['creating_multipost'] = True

# -----------------------
# Send post (choose and send) -- supports media+caption+buttons
# -----------------------
def menu_send_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    channels = load_json(CHANNEL_FILE)
    if not posts:
        q.message.reply_text("❗ কোনো পোস্ট নেই। আগে Create post দিয়ে পোস্ট যোগ করো।", reply_markup=back_to_menu_kb())
        return
    if not channels:
        q.message.reply_text("❗ কোনো চ্যানেল নেই। Add channel দিয়ে যোগ করো।", reply_markup=back_to_menu_kb())
        return

    kb = []
    for p in posts:
        kb.append([InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"send_post_{p['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    q.message.reply_text("📤 কোন পোস্ট পাঠাতে চাও?", reply_markup=InlineKeyboardMarkup(kb))

def send_post_selected(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    post_id = int(q.data.split("_")[-1])
    posts = load_json(POST_FILE)
    post = next((x for x in posts if x["id"] == post_id), None)
    if not post:
        q.message.reply_text("❌ পোস্ট পাওয়া যায়নি।", reply_markup=back_to_menu_kb())
        return

    channels = load_json(CHANNEL_FILE)
    sent = 0
    for ch in channels:
        try:
            markup = parse_buttons_from_text(post.get('buttons_raw', ''))
            caption = post.get("text", "")
            if post.get("media_type") == "photo":
                context.bot.send_photo(chat_id=ch['id'], photo=post["media_id"], caption=caption or None, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            elif post.get("media_type") == "video":
                context.bot.send_video(chat_id=ch['id'], video=post["media_id"], caption=caption or None, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            elif post.get("media_type") == "animation":
                context.bot.send_animation(chat_id=ch['id'], animation=post["media_id"], caption=caption or None, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            else:
                context.bot.send_message(chat_id=ch['id'], text=caption or "(No text)", parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            sent += 1
        except Exception as e:
            print("Send Error:", e)
    q.message.reply_text(f"✅ পোস্ট {sent} চ্যানেলে পাঠানো হয়েছে।", reply_markup=main_menu_kb())

# Send to All (choose post and broadcast) - slight variation kept
def menu_send_all_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("❗ কোনো পোস্ট নেই।", reply_markup=back_to_menu_kb())
        return
    kb = []
    for p in posts:
        kb.append([InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"choose_all_{p['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    q.message.reply_text("কোন পোস্ট All Channels-এ পাঠাবো?", reply_markup=InlineKeyboardMarkup(kb))

def choose_all_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[-1])
    posts = load_json(POST_FILE)
    post = next((x for x in posts if x['id'] == pid), None)
    if not post:
        q.message.reply_text("Post not found.", reply_markup=back_to_menu_kb())
        return
    channels = load_json(CHANNEL_FILE)
    sent = 0
    for ch in channels:
        try:
            markup = parse_buttons_from_text(post.get('buttons_raw', ''))
            caption = post.get("text", "")
            if post.get("media_type") == "photo":
                context.bot.send_photo(chat_id=ch['id'], photo=post["media_id"], caption=caption or None, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            elif post.get("media_type") == "video":
                context.bot.send_video(chat_id=ch['id'], video=post["media_id"], caption=caption or None, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            elif post.get("media_type") == "animation":
                context.bot.send_animation(chat_id=ch['id'], animation=post["media_id"], caption=caption or None, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            else:
                context.bot.send_message(chat_id=ch['id'], text=caption or "(No text)", parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            sent += 1
        except Exception as e:
            print("Send All Error:", e)
    q.message.reply_text(f"✅ পোস্ট {sent} চ্যানেলে পাঠানো হয়েছে!", reply_markup=main_menu_kb())

# Button guide
def menu_guide_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    text = (
        "*Button Format Guide*\n\n"
        "• Single button:\n"
        "`Button text - https://t.me/example`\n\n"
        "• Multiple buttons same line:\n"
        "`Button 1 - https://t.me/a && Button 2 - https://t.me/b`\n\n"
        "• Multiple rows of buttons:\n"
        "`Button text - https://t.me/LinkExample`\n`Button text - https://t.me/LinkExample`\n\n"
        "• Insert a button that displays a popup:\n"
        "`Button text - popup: Text of the popup`\n\n"
        "Example:\n`⎙ WATCH & DOWNLOAD ⎙ - https://t.me/fandub01 && 💬 GROUP - https://t.me/hindianime03`"
    )
    q.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_to_menu_kb())

# Generic callback handler (popup/alert/noop/other)
def generic_callback_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    data = q.data or ""
    if data.startswith("popup:") or data.startswith("alert:"):
        txt = data.split(":",1)[1].strip()
        try:
            q.answer(text=txt, show_alert=True)
        except:
            q.message.reply_text(txt)
    elif data == "noop":
        q.message.reply_text("🔘 বাটন ক্লিক হয়েছে (কোনো কার্য নেই)।")
    else:
        # fallback: echo action
        q.message.reply_text("🔘 বাটন ক্লিক: " + data)

# Back to menu
def back_to_menu_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    q.message.reply_text("↩️ মূল মেনুতে ফিরে আসা হলো", reply_markup=main_menu_kb())

# -----------------------
# Delete flows (channels/posts) - original behavior retained
# -----------------------
def menu_delete_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    kb = [
        [InlineKeyboardButton("🗑 Delete Post", callback_data="start_delete_post"),
         InlineKeyboardButton("🗑 Remove Channel", callback_data="start_delete_channel")],
        [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
    ]
    q.message.reply_text("Delete options:", reply_markup=InlineKeyboardMarkup(kb))

def start_delete_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("No posts to delete.", reply_markup=back_to_menu_kb())
        return
    kb = [[InlineKeyboardButton(f"Del {p['id']}", callback_data=f"del_post_{p['id']}")] for p in posts]
    kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    q.message.reply_text("Choose post to delete:", reply_markup=InlineKeyboardMarkup(kb))

def start_delete_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    channels = load_json(CHANNEL_FILE)
    if not channels:
        q.message.reply_text("No channels to remove.", reply_markup=back_to_menu_kb())
        return
    kb = [[InlineKeyboardButton(c['title'][:30], callback_data=f"remove_channel_{c['id']}")] for c in channels]
    kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    q.message.reply_text("Choose channel to remove:", reply_markup=InlineKeyboardMarkup(kb))

# -----------------------
# Main: register handlers and run
# -----------------------
def main():
    ensure_files()
    if not TOKEN:
        print("ERROR: BOT_TOKEN environment variable not set. Exiting.")
        return

    try:
        updater = Updater(TOKEN, use_context=True)
        dp = updater.dispatcher

        # Command
        dp.add_handler(CommandHandler("start", start))

        # Menu callbacks
        dp.add_handler(CallbackQueryHandler(menu_add_channel_cb, pattern="^menu_add_channel$"))
        dp.add_handler(CallbackQueryHandler(menu_channel_list_cb, pattern="^menu_channel_list$"))
        dp.add_handler(CallbackQueryHandler(menu_create_post_cb, pattern="^menu_create_post$"))
        dp.add_handler(CallbackQueryHandler(menu_my_posts_cb, pattern="^menu_my_posts$"))
        dp.add_handler(CallbackQueryHandler(menu_send_post_cb, pattern="^menu_send_post$"))
        dp.add_handler(CallbackQueryHandler(menu_send_all_cb, pattern="^menu_send_all$"))
        dp.add_handler(CallbackQueryHandler(menu_multipost_cb, pattern="^menu_multipost$"))
        dp.add_handler(CallbackQueryHandler(menu_edit_post_cb, pattern="^menu_edit_post$"))
        dp.add_handler(CallbackQueryHandler(menu_delete_cb, pattern="^menu_delete$"))
        dp.add_handler(CallbackQueryHandler(menu_guide_cb, pattern="^menu_guide$"))
        dp.add_handler(CallbackQueryHandler(back_to_menu_cb, pattern="^back_to_menu$"))

        # dynamic callbacks (view/remove/edit/send)
        dp.add_handler(CallbackQueryHandler(view_channel_cb, pattern=r"^view_channel_"))
        dp.add_handler(CallbackQueryHandler(remove_channel_cb, pattern=r"^remove_channel_"))
        dp.add_handler(CallbackQueryHandler(view_post_cb, pattern=r"^view_post_"))
        dp.add_handler(CallbackQueryHandler(del_post_cb, pattern=r"^del_post_"))
        dp.add_handler(CallbackQueryHandler(choose_edit_post_cb, pattern=r"^edit_post_"))
        dp.add_handler(CallbackQueryHandler(send_post_selected, pattern=r"^send_post_"))
        dp.add_handler(CallbackQueryHandler(choose_all_cb, pattern=r"^choose_all_"))

        # add buttons and caption choices
        dp.add_handler(CallbackQueryHandler(add_buttons_cb, pattern=r"^add_buttons_"))
        dp.add_handler(CallbackQueryHandler(caption_choice_cb, pattern=r"^(add_caption|skip_caption)$"))

        # delete flows
        dp.add_handler(CallbackQueryHandler(start_delete_post_cb, pattern=r"^start_delete_post$"))
        dp.add_handler(CallbackQueryHandler(start_delete_channel_cb, pattern=r"^start_delete_channel$"))

        # generic callback (popup/alert/noop)
        dp.add_handler(CallbackQueryHandler(generic_callback_cb, pattern=r"^(popup:|alert:|noop)"))

        # media, forward, and text handlers
        dp.add_handler(MessageHandler(Filters.forwarded & Filters.chat_type.private, forward_handler))
        dp.add_handler(MessageHandler(Filters.photo | Filters.video | Filters.animation, media_handler))
        # a single unified text handler to handle: creating_post, editing_post, multipost, awaiting caption, awaiting buttons
        dp.add_handler(MessageHandler(Filters.text & Filters.chat_type.private, save_text_handler))

        # Send and other small callbacks
        dp.add_handler(CallbackQueryHandler(menu_send_post_cb, pattern="^menu_send_post$"))
        dp.add_handler(CallbackQueryHandler(menu_send_all_cb, pattern="^menu_send_all$"))
        dp.add_handler(CallbackQueryHandler(menu_multipost_cb, pattern="^menu_multipost$"))

        # small alias handlers to match previous naming expectations
        dp.add_handler(CallbackQueryHandler(add_buttons_cb, pattern=r"^add_buttons_"))
        dp.add_handler(CallbackQueryHandler(menu_channel_list_cb, pattern="^menu_channel_list$"))

        print("✅ Bot started successfully!")
        updater.start_polling()
        updater.idle()

    except Exception as e:
        print(f"❌ Bot startup failed: {e}")
        raise

# -----------------------
# Flask keep-alive (for Render)
# -----------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Telegram MultiPost Bot is running on Render!"

# -----------------------
# Run: start bot in a thread then Flask
# -----------------------
if __name__ == "__main__":
    # start bot in a separate thread so Flask can serve health checks
    t = threading.Thread(target=main)
    t.start()
    # run flask
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
