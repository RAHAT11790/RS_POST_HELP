import os
import json
import logging
import threading
import datetime
from flask import Flask
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler

# -----------------------
# Logging
# -----------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),  # Log to a file for debugging
        logging.StreamHandler()  # Also log to console
    ]
)
logger = logging.getLogger(__name__)

# -----------------------
# Config / Files
# -----------------------
TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_FILE = "channels.json"
POST_FILE = "posts.json"
SCHEDULED_POSTS_FILE = "scheduled_posts.json"

# -----------------------
# Helpers: JSON file IO
# -----------------------
def load_json(filename):
    if not os.path.exists(filename):
        logger.warning(f"File {filename} does not exist, returning empty list.")
        return []
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {filename}: {e}")
        return []

def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving {filename}: {e}")
        if 'update' in globals() and update:
            update.message.reply_text("❌ ফাইল সেভ করতে সমস্যা হয়েছে। দয়া করে আবার চেষ্টা করুন।")

def ensure_files():
    if not os.path.exists(CHANNEL_FILE):
        save_json(CHANNEL_FILE, [])
    if not os.path.exists(POST_FILE):
        save_json(POST_FILE, [])
    if not os.path.exists(SCHEDULED_POSTS_FILE):
        save_json(SCHEDULED_POSTS_FILE, [])

# -----------------------
# State Management for Back Navigation
# -----------------------
def push_state(context: CallbackContext, state: str, kb_data: dict = None):
    if 'previous_states' not in context.user_data:
        context.user_data['previous_states'] = []
    context.user_data['previous_states'].append({'state': state, 'kb_data': kb_data})

def pop_state(context: CallbackContext):
    if 'previous_states' in context.user_data and context.user_data['previous_states']:
        return context.user_data['previous_states'].pop()
    return None

# -----------------------
# Button parser
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
                if action.startswith(("http://", "https://", "tg://", "https://t.me")):
                    row.append(InlineKeyboardButton(title[:64], url=action))
                else:
                    row.append(InlineKeyboardButton(title[:64], callback_data=action))
            else:
                row.append(InlineKeyboardButton(p[:64], callback_data="noop"))
        if row:
            rows.append(row)
    return InlineKeyboardMarkup(rows) if rows else None

# -----------------------
# UI keyboards
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
        [InlineKeyboardButton("📅 Schedule Post", callback_data="menu_schedule_post")],
        [InlineKeyboardButton("🗑 Delete", callback_data="menu_delete"),
         InlineKeyboardButton("📘 Button Guide", callback_data="menu_guide")]
    ]
    return InlineKeyboardMarkup(kb)

def back_to_menu_kb(text="↩️ Back"):
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
# Add Channel flow
# -----------------------
def menu_add_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
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
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
    channels = load_json(CHANNEL_FILE)
    if not channels:
        q.message.reply_text("📭 এখনো কোনো চ্যানেল নেই। Add channel দিয়ে চ্যানেল যোগ করো।", reply_markup=back_to_menu_kb())
        return

    kb = []
    for ch in channels:
        kb.append([InlineKeyboardButton(ch['title'][:40], callback_data=f"view_channel_{ch['id']}"),
                   InlineKeyboardButton("❌ Remove", callback_data=f"remove_channel_{ch['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
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
# Create post flow
# -----------------------
def menu_create_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
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

def save_text_handler(update: Update, context: CallbackContext):
    user = context.user_data

    # 1) If user is adding buttons to an existing post
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
        kb = [
            [InlineKeyboardButton("📤 Send Post", callback_data=f"send_post_{post_id}")],
            [InlineKeyboardButton("🌐 Send to All", callback_data=f"choose_all_{post_id}")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        update.message.reply_text(
            "✅ বাটন সংরক্ষণ হয়েছে! এখন চাইলে পোস্ট পাঠাও:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        user.pop('awaiting_buttons_for_post_id', None)
        return

    # 2) If user is awaiting caption text for a media
    if user.get('awaiting_caption_text'):
        caption = update.message.text or ""
        fid = user.get('pending_file_id')
        mtype = user.get('pending_type')
        posts = load_json(POST_FILE)
        post_id = len(posts) + 1
        posts.append({
            "id": post_id,
            "text": caption,
            "buttons_raw": "",
            "media_id": fid,
            "media_type": mtype
        })
        save_json(POST_FILE, posts)
        kb = [
            [InlineKeyboardButton("➕ Add Buttons", callback_data=f"add_buttons_{post_id}")],
            [InlineKeyboardButton("📤 Send Post", callback_data=f"send_post_{post_id}")],
            [InlineKeyboardButton("🌐 Send to All", callback_data=f"choose_all_{post_id}")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        update.message.reply_text(
            "✅ ক্যাপশনসহ মিডিয়া সেভ হয়েছে! এখন চাইলে বাটন যোগ করো বা সরাসরি পাঠাও:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        user.pop('awaiting_caption_text', None)
        user.pop('pending_file_id', None)
        user.pop('pending_type', None)
        return

    # 3) Multipost saving
    if user.get('creating_multipost'):
        text = update.message.text or ""
        raw = text
        parts = [p.strip() for p in raw.split("---") if p.strip()]
        posts = load_json(POST_FILE)
        new_posts = []
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
            new_posts.append({
                "id": len(posts) + len(new_posts) + 1,
                "text": main_text or "(empty)",
                "buttons_raw": btn_text,
                "media_id": None,
                "media_type": None
            })
        posts.extend(new_posts)
        save_json(POST_FILE, posts)
        kb = [[InlineKeyboardButton(f"📄 Send Post {p['id']}", callback_data=f"send_post_{p['id']}")] for p in new_posts]
        kb.append([InlineKeyboardButton("🌐 Send All New Posts", callback_data="send_all_new_posts")])
        kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
        update.message.reply_text(
            f"✅ মোট {len(new_posts)}টি পোস্ট যোগ করা হয়েছে! এখন পাঠাতে চাইলে নিচের অপশন ব্যবহার করো:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        user['new_multipost_ids'] = [p['id'] for p in new_posts]
        user.pop('creating_multipost', None)
        return

    # 4) Editing existing post
    if user.get('editing_post'):
        pid = user.get('editing_post')
        text = update.message.text or ""
        posts = load_json(POST_FILE)
        p = next((x for x in posts if x['id'] == pid), None)
        if not p:
            update.message.reply_text("❌ পোস্ট পাওয়া যায়নি।", reply_markup=main_menu_kb())
            user.pop('editing_post', None)
            return
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

    # 5) Regular "create_post" text handling
    if user.get('creating_post'):
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
        main_text = "\n".join(main_lines).strip()
        btn_text = "\n".join(btn_lines).strip()
        post_id = len(posts) + 1
        posts.append({"id": post_id, "text": main_text, "buttons_raw": btn_text, "media_id": None, "media_type": None})
        save_json(POST_FILE, posts)
        kb = [
            [InlineKeyboardButton("➕ Add Buttons", callback_data=f"add_buttons_{post_id}")],
            [InlineKeyboardButton("📤 Send Post", callback_data=f"send_post_{post_id}")],
            [InlineKeyboardButton("🌐 Send to All", callback_data=f"choose_all_{post_id}")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        update.message.reply_text(
            "✅ পোস্ট সংরক্ষণ করা হয়েছে! এখন চাইলে বাটন যোগ করো বা সরাসরি পাঠাও:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        user.pop('creating_post', None)
        return

    # 6) Schedule time handler
    if user.get('awaiting_schedule_time'):
        time_str = update.message.text
        try:
            schedule_time = datetime.datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            update.message.reply_text("❌ ভুল ফরম্যাট। দয়া করে HH:MM ফরম্যাটে লিখুন (যেমন 14:30)।", reply_markup=back_to_menu_kb())
            return
        post_id = user.get('scheduling_post_id')
        schedule_type = user.get('schedule_type')
        scheduled_posts = load_json(SCHEDULED_POSTS_FILE)
        scheduled_posts.append({
            "post_id": post_id,
            "time": time_str,
            "type": "one_time" if schedule_type == "schedule_one_time" else "all_time"
        })
        save_json(SCHEDULED_POSTS_FILE, scheduled_posts)
        update.message.reply_text(
            f"✅ পোস্ট {post_id} সিডিউল করা হয়েছে {time_str}-এ {'একবার' if schedule_type == 'schedule_one_time' else 'প্রতিদিন'} পোস্ট করার জন্য!",
            reply_markup=main_menu_kb()
        )
        user.pop('awaiting_schedule_time', None)
        user.pop('scheduling_post_id', None)
        user.pop('schedule_type', None)
        return

# -----------------------
# Media handler
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

    push_state(context, "create_post", {'reply_markup': back_to_menu_kb()})
    if msg.caption:
        posts = load_json(POST_FILE)
        post_id = len(posts) + 1
        posts.append({
            "id": post_id,
            "text": msg.caption,
            "buttons_raw": "",
            "media_id": fid,
            "media_type": mtype
        })
        save_json(POST_FILE, posts)
        kb = [
            [InlineKeyboardButton("➕ Add Buttons", callback_data=f"add_buttons_{post_id}")],
            [InlineKeyboardButton("📤 Send Post", callback_data=f"send_post_{post_id}")],
            [InlineKeyboardButton("🌐 Send to All", callback_data=f"choose_all_{post_id}")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        msg.reply_text(
            "✅ মিডিয়া ও ক্যাপশন সেভ হয়েছে! এখন চাইলে বাটন যোগ করো বা সরাসরি পাঠাও:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    context.user_data['pending_file_id'] = fid
    context.user_data['pending_type'] = mtype
    kb = [
        [InlineKeyboardButton("✍️ Add Caption", callback_data="add_caption")],
        [InlineKeyboardButton("⏭️ Skip (no caption)", callback_data="skip_caption")],
        [InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")]
    ]
    msg.reply_text("📝 আপনি কি ক্যাপশন যোগ করতে চান?", reply_markup=InlineKeyboardMarkup(kb))

def caption_choice_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    data = q.data
    push_state(context, "media_caption_choice", {'reply_markup': back_to_menu_kb()})
    if data == "add_caption":
        q.message.reply_text("✍️ এখন ক্যাপশন লিখে পাঠান:", reply_markup=back_to_menu_kb())
        context.user_data['awaiting_caption_text'] = True
    elif data == "skip_caption":
        fid = context.user_data.get('pending_file_id')
        mtype = context.user_data.get('pending_type')
        posts = load_json(POST_FILE)
        post_id = len(posts) + 1
        posts.append({
            "id": post_id,
            "text": "",
            "buttons_raw": "",
            "media_id": fid,
            "media_type": mtype
        })
        save_json(POST_FILE, posts)
        kb = [
            [InlineKeyboardButton("➕ Add Buttons", callback_data=f"add_buttons_{post_id}")],
            [InlineKeyboardButton("📤 Send Post", callback_data=f"send_post_{post_id}")],
            [InlineKeyboardButton("🌐 Send to All", callback_data=f"choose_all_{post_id}")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        q.message.reply_text(
            "✅ মিডিয়া (ক্যাপশন ছাড়া) সেভ করা হয়েছে! এখন চাইলে বাটন যোগ করো বা সরাসরি পাঠাও:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        context.user_data.pop('pending_file_id', None)
        context.user_data.pop('pending_type', None)
    else:
        q.message.reply_text("❌ অজানা অপশন", reply_markup=main_menu_kb())

def add_buttons_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
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
    push_state(context, "post_options", {'reply_markup': InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Buttons", callback_data=f"add_buttons_{pid}")],
        [InlineKeyboardButton("📤 Send Post", callback_data=f"send_post_{pid}")],
        [InlineKeyboardButton("🌐 Send to All", callback_data=f"choose_all_{pid}")],
        [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
    ])})
    context.user_data['awaiting_buttons_for_post_id'] = pid
    kb = [
        [InlineKeyboardButton("📘 Button Guide", callback_data="menu_guide")],
        [InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")]
    ]
    q.message.reply_text(
        "✍️ এখন বাটন লাইন পাঠাও (উদাহরণ):\n`⎙ WATCH & DOWNLOAD ⎙ - https://t.me/fandub01 && 💬 GROUP - https://t.me/hindianime03`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb)
    )

# -----------------------
# My posts / view / delete / edit flows
# -----------------------
def menu_my_posts_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("📭 কোনো পোস্ট নেই। Create post দিয়ে পোস্ট যোগ করো।", reply_markup=back_to_menu_kb())
        return
    kb = []
    for p in posts:
        kb.append([InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"view_post_{p['id']}"),
                   InlineKeyboardButton("🗑 Delete", callback_data=f"del_post_{p['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
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
        logger.error(f"Error displaying post {pid}: {e}")
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
    q.message.reply_text("✅ পোস্ট মুছে দেয়া হয়েছে।", reply_markup=main_menu_kb())

# Edit post flow
def menu_edit_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("❗ কোনো পোস্ট নেই।", reply_markup=back_to_menu_kb())
        return
    kb = [[InlineKeyboardButton(f"✏️ Edit {p['id']}", callback_data=f"edit_post_{p['id']}")] for p in posts]
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
    q.message.reply_text("✏️ কোন পোস্ট এডিট করতে চাও?", reply_markup=InlineKeyboardMarkup(kb))

def choose_edit_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[-1])
    context.user_data['editing_post'] = pid
    push_state(context, "edit_post_select", {'reply_markup': back_to_menu_kb()})
    q.message.reply_text("✏️ নতুন টেক্সট বা বাটন লাইন পাঠাও (বাটন ফরম্যাট দেখতে Guide চাপো).", reply_markup=back_to_menu_kb())

# -----------------------
# Multipost
# -----------------------
def menu_multipost_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
    q.message.reply_text(
        "🧾 Multipost: একসাথে একাধিক পোস্ট যুক্ত করতে প্রতিটি পোস্ট আলাদা করতে `---` ব্যবহার করো।\n\n"
        "উদাহরণ:\nPost text 1\nbutton - https://t.me/a\n---\nPost text 2\nbutton - https://t.me/b && button2 - https://t.me/c",
        reply_markup=back_to_menu_kb()
    )
    context.user_data['creating_multipost'] = True

# -----------------------
# Send post
# -----------------------
def menu_send_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
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
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
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
            logger.error(f"Error sending post {post_id} to channel {ch['id']}: {e}")
    q.message.reply_text(f"✅ পোস্ট {sent} চ্যানেলে পাঠানো হয়েছে।", reply_markup=main_menu_kb())

def menu_send_all_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("❗ কোনো পোস্ট নেই।", reply_markup=back_to_menu_kb())
        return
    kb = []
    for p in posts:
        kb.append([InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"choose_all_{p['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
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
            logger.error(f"Error sending post {pid} to channel {ch['id']}: {e}")
    q.message.reply_text(f"✅ পোস্ট {sent} চ্যানেলে পাঠানো হয়েছে!", reply_markup=main_menu_kb())

def send_all_new_posts_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    new_post_ids = context.user_data.get('new_multipost_ids', [])
    posts = load_json(POST_FILE)
    channels = load_json(CHANNEL_FILE)
    sent = 0
    for pid in new_post_ids:
        post = next((x for x in posts if x['id'] == pid), None)
        if not post:
            continue
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
                logger.error(f"Error sending new post {pid} to channel {ch['id']}: {e}")
    q.message.reply_text(f"✅ মোট {sent}টি পোস্ট পাঠানো হয়েছে!", reply_markup=main_menu_kb())
    context.user_data.pop('new_multipost_ids', None)

# -----------------------
# Schedule Post
# -----------------------
def menu_schedule_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("❗ কোনো পোস্ট নেই। আগে Create post দিয়ে পোস্ট যোগ করো।", reply_markup=back_to_menu_kb())
        return
    kb = []
    for p in posts:
        kb.append([InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"schedule_post_{p['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
    q.message.reply_text("📅 কোন পোস্ট সিডিউল করতে চাও?", reply_markup=InlineKeyboardMarkup(kb))

def schedule_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    post_id = int(q.data.split("_")[-1])
    context.user_data['scheduling_post_id'] = post_id
    push_state(context, "schedule_post_select", {'reply_markup': back_to_menu_kb()})
    kb = [
        [InlineKeyboardButton("⏰ One-Time Post", callback_data="schedule_one_time")],
        [InlineKeyboardButton("🔄 All-Time Send", callback_data="schedule_all_time")],
        [InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")]
    ]
    q.message.reply_text(
        "📅 পোস্ট সিডিউল করার ধরন বেছে নিন:\n"
        "• One-Time Post: একবার পোস্ট হবে এবং ডিলিট হয়ে যাবে।\n"
        "• All-Time Send: প্রতিদিন নির্দিষ্ট সময়ে পোস্ট হবে।",
        reply_markup=InlineKeyboardMarkup(kb)
    )

def schedule_type_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    schedule_type = q.data
    if schedule_type not in ["schedule_one_time", "schedule_all_time"]:
        q.message.reply_text("❌ অজানা অপশন।", reply_markup=back_to_menu_kb())
        return
    context.user_data['schedule_type'] = schedule_type
    push_state(context, "schedule_type_select", {'reply_markup': back_to_menu_kb()})
    q.message.reply_text(
        "⏰ পোস্ট করার সময় লিখুন (ফরম্যাট: HH:MM, 24-ঘণ্টা ফরম্যাটে, যেমন 14:30):",
        reply_markup=back_to_menu_kb()
    )
    context.user_data['awaiting_schedule_time'] = True

def check_scheduled_posts(context: CallbackContext):
    current_time = datetime.datetime.now().strftime("%H:%M")
    scheduled_posts = load_json(SCHEDULED_POSTS_FILE)
    posts = load_json(POST_FILE)
    channels = load_json(CHANNEL_FILE)
    posts_to_remove = []
    for sp in scheduled_posts:
        if sp['time'] == current_time:
            post = next((p for p in posts if p['id'] == sp['post_id']), None)
            if not post:
                continue
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
                except Exception as e:
                    logger.error(f"Error sending scheduled post {sp['post_id']} to channel {ch['id']}: {e}")
            if sp['type'] == "one_time":
                posts_to_remove.append(sp)
    scheduled_posts = [sp for sp in scheduled_posts if sp not in posts_to_remove]
    save_json(SCHEDULED_POSTS_FILE, scheduled_posts)

# -----------------------
# Button guide
# -----------------------
def menu_guide_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
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

# -----------------------
# Generic callback
# -----------------------
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
        q.message.reply_text("🔘 বাটন ক্লিক: " + data)

# -----------------------
# Back to menu
# -----------------------
def back_to_menu_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    prev_state = pop_state(context)
    if prev_state:
        state = prev_state.get('state')
        kb_data = prev_state.get('kb_data', {})
        if state == "main_menu":
            q.message.reply_text("↩️ মূল মেনুতে ফিরে আসা হলো", reply_markup=main_menu_kb())
        elif state == "create_post":
            context.user_data['creating_post'] = True
            q.message.reply_text(
                "📝 পোস্ট তৈরি শুরু হয়েছে।\n\n"
                "আপনি চাইলে *প্রথমে মিডিয়া* (ছবি/ভিডিও) পাঠাতে পারো — মিডিয়া পাঠালে বট সেটি সেভ করবে এবং পরে আপনি বাটন যোগ করতে পারবেন।\n"
                "অথবা সরাসরি টেক্সট পাঠালে সেটাও পোস্ট হিসেবে সেভ হবে।",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_data.get('reply_markup', back_to_menu_kb())
            )
        elif state == "media_caption_choice":
            fid = context.user_data.get('pending_file_id')
            mtype = context.user_data.get('pending_type')
            if fid and mtype:
                kb = [
                    [InlineKeyboardButton("✍️ Add Caption", callback_data="add_caption")],
                    [InlineKeyboardButton("⏭️ Skip (no caption)", callback_data="skip_caption")],
                    [InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")]
                ]
                q.message.reply_text("📝 আপনি কি ক্যাপশন যোগ করতে চান?", reply_markup=InlineKeyboardMarkup(kb))
            else:
                q.message.reply_text("❌ মিডিয়া পাওয়া যায়নি।", reply_markup=main_menu_kb())
        elif state == "post_options":
            q.message.reply_text("📝 পোস্ট অপশন:", reply_markup=kb_data.get('reply_markup', main_menu_kb()))
        elif state == "schedule_post_select":
            posts = load_json(POST_FILE)
            kb = []
            for p in posts:
                kb.append([InlineKeyboardButton(f"📄 Post {p['id']}", callback_data=f"schedule_post_{p['id']}")])
            kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
            q.message.reply_text("📅 কোন পোস্ট সিডিউল করতে চাও?", reply_markup=InlineKeyboardMarkup(kb))
        elif state == "schedule_type_select":
            post_id = context.user_data.get('scheduling_post_id')
            kb = [
                [InlineKeyboardButton("⏰ One-Time Post", callback_data="schedule_one_time")],
                [InlineKeyboardButton("🔄 All-Time Send", callback_data="schedule_all_time")],
                [InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")]
            ]
            q.message.reply_text(
                "📅 পোস্ট সিডিউল করার ধরন বেছে নিন:\n"
                "• One-Time Post: একবার পোস্ট হবে এবং ডিলিট হয়ে যাবে।\n"
                "• All-Time Send: প্রতিদিন নির্দিষ্ট সময়ে পোস্ট হবে।",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        else:
            q.message.reply_text("↩️ মূল মেনুতে ফিরে আসা হলো", reply_markup=main_menu_kb())
    else:
        q.message.reply_text("↩️ মূল মেনুতে ফিরে আসা হলো", reply_markup=main_menu_kb())
    context.user_data.clear()

# -----------------------
# Delete flows
# -----------------------
def menu_delete_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "main_menu", {'reply_markup': main_menu_kb()})
    kb = [
        [InlineKeyboardButton("🗑 Delete Post", callback_data="start_delete_post"),
         InlineKeyboardButton("🗑 Remove Channel", callback_data="start_delete_channel")],
        [InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")]
    ]
    q.message.reply_text("Delete options:", reply_markup=InlineKeyboardMarkup(kb))

def start_delete_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "delete_menu", {'reply_markup': InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Delete Post", callback_data="start_delete_post"),
         InlineKeyboardButton("🗑 Remove Channel", callback_data="start_delete_channel")],
        [InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")]
    ])})
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("No posts to delete.", reply_markup=back_to_menu_kb())
        return
    kb = [[InlineKeyboardButton(f"Del {p['id']}", callback_data=f"del_post_{p['id']}")] for p in posts]
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
    q.message.reply_text("Choose post to delete:", reply_markup=InlineKeyboardMarkup(kb))

def start_delete_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    push_state(context, "delete_menu", {'reply_markup': InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Delete Post", callback_data="start_delete_post"),
         InlineKeyboardButton("🗑 Remove Channel", callback_data="start_delete_channel")],
        [InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")]
    ])})
    channels = load_json(CHANNEL_FILE)
    if not channels:
        q.message.reply_text("No channels to remove.", reply_markup=back_to_menu_kb())
        return
    kb = [[InlineKeyboardButton(c['title'][:30], callback_data=f"remove_channel_{c['id']}")] for c in channels]
    kb.append([InlineKeyboardButton("↩️ Back", callback_data="back_to_menu")])
    q.message.reply_text("Choose channel to remove:", reply_markup=InlineKeyboardMarkup(kb))

# -----------------------
# Main: register handlers and run
# -----------------------
def main():
    ensure_files()
    if not TOKEN:
        logger.error("BOT_TOKEN environment variable not set. Exiting.")
        return

    try:
        updater = Updater(TOKEN, use_context=True)
        dp = updater.dispatcher

        # Schedule posts
        scheduler = BackgroundScheduler(timezone="Asia/Dhaka")
        scheduler.add_job(check_scheduled_posts, 'interval', minutes=1, args=[dp.bot.get_context()])
        scheduler.start()
        logger.info("Scheduler started with Asia/Dhaka timezone.")

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
        dp.add_handler(CallbackQueryHandler(menu_schedule_post_cb, pattern="^menu_schedule_post$"))
        dp.add_handler(CallbackQueryHandler(menu_delete_cb, pattern="^menu_delete$"))
        dp.add_handler(CallbackQueryHandler(menu_guide_cb, pattern="^menu_guide$"))
        dp.add_handler(CallbackQueryHandler(back_to_menu_cb, pattern="^back_to_menu$"))

        # Dynamic callbacks
        dp.add_handler(CallbackQueryHandler(view_channel_cb, pattern=r"^view_channel_"))
        dp.add_handler(CallbackQueryHandler(remove_channel_cb, pattern=r"^remove_channel_"))
        dp.add_handler(CallbackQueryHandler(view_post_cb, pattern=r"^view_post_"))
        dp.add_handler(CallbackQueryHandler(del_post_cb, pattern=r"^del_post_"))
        dp.add_handler(CallbackQueryHandler(choose_edit_post_cb, pattern=r"^edit_post_"))
        dp.add_handler(CallbackQueryHandler(send_post_selected, pattern=r"^send_post_"))
        dp.add_handler(CallbackQueryHandler(choose_all_cb, pattern=r"^choose_all_"))
        dp.add_handler(CallbackQueryHandler(send_all_new_posts_cb, pattern="^send_all_new_posts$"))
        dp.add_handler(CallbackQueryHandler(schedule_post_cb, pattern=r"^schedule_post_"))
        dp.add_handler(CallbackQueryHandler(schedule_type_cb, pattern=r"^(schedule_one_time|schedule_all_time)$"))

        # Add buttons and caption choices
        dp.add_handler(CallbackQueryHandler(add_buttons_cb, pattern=r"^add_buttons_"))
        dp.add_handler(CallbackQueryHandler(caption_choice_cb, pattern=r"^(add_caption|skip_caption)$"))

        # Delete flows
        dp.add_handler(CallbackQueryHandler(start_delete_post_cb, pattern=r"^start_delete_post$"))
        dp.add_handler(CallbackQueryHandler(start_delete_channel_cb, pattern=r"^start_delete_channel$"))

        # Generic callback
        dp.add_handler(CallbackQueryHandler(generic_callback_cb, pattern=r"^(popup:|alert:|noop)"))

        # Handlers
        dp.add_handler(MessageHandler(Filters.forwarded & Filters.chat_type.private, forward_handler))
        dp.add_handler(MessageHandler(Filters.photo | Filters.video | Filters.animation, media_handler))
        dp.add_handler(MessageHandler(Filters.text & Filters.chat_type.private, save_text_handler))

        logger.info("Bot started successfully!")
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"Bot startup failed: {e}")
        raise

# -----------------------
# Flask keep-alive (for Render)
# -----------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Telegram MultiPost Bot is running on Render!"

# -----------------------
# Run
# -----------------------
if __name__ == "__main__":
    t = threading.Thread(target=main)
    t.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
