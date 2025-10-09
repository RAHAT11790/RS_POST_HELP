import os
import json
import logging
import threading
import time
from datetime import datetime, timedelta
import pytz
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
SCHEDULE_FILE = "scheduled_posts.json"

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
    if not os.path.exists(SCHEDULE_FILE):
        save_json(SCHEDULE_FILE, [])

# -----------------------
# Step stack helpers (for one-step back behavior)
# -----------------------
def push_step(context: CallbackContext, name: str, info: dict = None):
    if 'step_stack' not in context.user_data:
        context.user_data['step_stack'] = []
    context.user_data['step_stack'].append({'name': name, 'info': info or {}})

def pop_step(context: CallbackContext):
    if 'step_stack' in context.user_data and context.user_data['step_stack']:
        return context.user_data['step_stack'].pop()
    return None

def peek_prev_step(context: CallbackContext):
    if 'step_stack' in context.user_data and len(context.user_data['step_stack']) >= 1:
        return context.user_data['step_stack'][-1]
    return None

def clear_steps(context: CallbackContext):
    context.user_data.pop('step_stack', None)

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
                title = title.strip()[:64]
                action = action.strip()
                if action.startswith(("http://", "https://", "tg://", "https://t.me")):
                    row.append(InlineKeyboardButton(title, url=action))
                elif action.startswith(("popup:", "alert:")):
                    row.append(InlineKeyboardButton(title, callback_data=action))
                else:
                    row.append(InlineKeyboardButton(title, callback_data=action[:64]))
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
        [InlineKeyboardButton("🗑 Delete", callback_data="menu_delete"),
         InlineKeyboardButton("🕒 Schedule Post", callback_data="menu_schedule")],
        [InlineKeyboardButton("⏰ Manage Schedule", callback_data="menu_schedule_manage"),
         InlineKeyboardButton("📘 Button Guide", callback_data="menu_guide")]
    ]
    return InlineKeyboardMarkup(kb)

def back_to_menu_kb(text="↩️ Back to Menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data="back_to_menu")]])

def step_back_kb(text="↩️ Back (one step)"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data="step_back")]])

# -----------------------
# /start
# -----------------------
def start(update: Update, context: CallbackContext):
    context.user_data.clear()
    clear_steps(context)
    txt = (
        "👋 *স্বাগতম — Multi Channel Poster Bot!* \n\n"
        "নিচের বাটনগুলো দিয়ে কাজগুলো করা যাবে।\n\n"
        "📘 বাটন গাইড দেখতে 'Button Guide' বাটনে চাপ দাও।"
    )
    update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())

# -----------------------
# Add Channel
# -----------------------
def menu_add_channel_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    context.user_data['expecting_forward_for_add'] = True
    push_step(context, 'expecting_forward_for_add')
    q.message.reply_text(
        "📩 চ্যানেল অ্যাড করতে, *চ্যানেল থেকে একটি মেসেজ ফরওয়ার্ড* করে এখানে পাঠাও।\n\n"
        "⚠️ নিশ্চিত করো বটটি সেই চ্যানেলে *admin* আছে।",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=step_back_kb()
    )

def forward_handler(update: Update, context: CallbackContext):
    msg = update.message
    if not msg.forward_from_chat:
        update.message.reply_text("❌ এটি চ্যানেল থেকে ফরওয়ার্ড করা মেসেজ নয়।", reply_markup=main_menu_kb())
        return

    chat = msg.forward_from_chat
    if chat.type != 'channel':
        update.message.reply_text("❌ ফরওয়ার্ড করা মেসেজটি একটি চ্যানেলের নয়।", reply_markup=main_menu_kb())
        return

    channels = load_json(CHANNEL_FILE)
    existing_ids = [c['id'] for c in channels]
    if chat.id in existing_ids:
        update.message.reply_text(f"⚠️ চ্যানেল *{chat.title}* আগে থেকেই যুক্ত আছে।", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())
        context.user_data.pop('expecting_forward_for_add', None)
        pop_step(context)
        return

    channels.append({'id': chat.id, 'title': chat.title or str(chat.id)})
    save_json(CHANNEL_FILE, channels)
    update.message.reply_text(f"✅ চ্যানেল *{chat.title}* সফলভাবে যুক্ত হয়েছে!", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())
    context.user_data.pop('expecting_forward_for_add', None)
    pop_step(context)

# -----------------------
# Channel list & remove/view
# -----------------------
def menu_channel_list_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    channels = load_json(CHANNEL_FILE)
    if not channels:
        q.message.reply_text("📭 এখনো কোনো চ্যানেল নেই। Add channel দিয়ে চ্যানেল যোগ করো।", reply_markup=main_menu_kb())
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
# Create post flow
# -----------------------
def menu_create_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    context.user_data.pop('creating_post', None)
    context.user_data.pop('pending_file_id', None)
    context.user_data.pop('pending_type', None)
    clear_steps(context)
    context.user_data['creating_post'] = True
    push_step(context, 'creating_post')
    q.message.reply_text(
        "📝 পোস্ট তৈরি শুরু হয়েছে।\n\n"
        "আপনি চাইলে *প্রথমে মিডিয়া* (ছবি/ভিডিও) পাঠাতে পারো — মিডিয়া পাঠালে বট সেটি সেভ করবে এবং পরে আপনি বাটন যোগ করতে পারবেন।\n"
        "অথবা সরাসরি টেক্সট পাঠালে সেটাও পোস্ট হিসেবে সেভ হবে।\n\n"
        "📎 মিডিয়া পাঠালে বট ক্যাপশন চেক করবে — যদি না থাকে তাহলে আপনি *Add Caption* বা *Skip* করে এগোতে পারবেন।",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=step_back_kb()
    )

def save_text_handler(update: Update, context: CallbackContext):
    user = context.user_data

    if user.get('awaiting_buttons_for_post_id'):
        post_id = user.get('awaiting_buttons_for_post_id')
        buttons_raw = update.message.text or ""
        posts = load_json(POST_FILE)
        p = next((x for x in posts if x['id'] == post_id), None)
        if not p:
            update.message.reply_text("❌ পোস্ট পাওয়া যায়নি।", reply_markup=main_menu_kb())
            user.pop('awaiting_buttons_for_post_id', None)
            pop_step(context)
            return
        p['buttons_raw'] = buttons_raw
        save_json(POST_FILE, posts)
        kb = [
            [InlineKeyboardButton("📤 Send Post", callback_data=f"send_post_{post_id}")],
            [InlineKeyboardButton("🕒 Schedule Post", callback_data=f"schedule_post_{post_id}")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        update.message.reply_text(
            "✅ বাটন সংরক্ষণ হয়েছে! এখন চাইলে পোস্ট পাঠাও বা শিডিউল করো:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        user.pop('awaiting_buttons_for_post_id', None)
        pop_step(context)
        return

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
        new_id = len(posts)
        kb = [
            [InlineKeyboardButton("➕ Add Buttons", callback_data=f"add_buttons_{new_id}")],
            [InlineKeyboardButton("📤 Send Post", callback_data=f"send_post_{new_id}")],
            [InlineKeyboardButton("🕒 Schedule Post", callback_data=f"schedule_post_{new_id}")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        update.message.reply_text("✅ ক্যাপশনসহ মিডিয়া সেভ হয়েছে! এখন চাইলে বাটন যোগ করো বা সরাসরি পাঠাও:", reply_markup=InlineKeyboardMarkup(kb))
        user.pop('awaiting_caption_text', None)
        user.pop('pending_file_id', None)
        user.pop('pending_type', None)
        pop_step(context)
        return

    if user.get('awaiting_buttons_for_multipost'):
        buttons_raw = update.message.text or ""
        if 'multipost_temp' in context.user_data:
            context.user_data['multipost_temp']['buttons_raw'] = buttons_raw
            kb = [
                [InlineKeyboardButton("💾 Save Post", callback_data="save_multipost")],
                [InlineKeyboardButton("📤 Send All Posts", callback_data="send_all_multipost")],
                [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
            ]
            update.message.reply_text(
                "✅ বাটন সংরক্ষণ হয়েছে! এখন সেভ করো বা সব পোস্ট পাঠাও:",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            user.pop('awaiting_buttons_for_multipost', None)
            pop_step(context)
        return

    if user.get('awaiting_caption_text_multipost'):
        caption = update.message.text or ""
        fid = user.get('pending_file_id')
        mtype = user.get('pending_type')
        context.user_data['multipost_temp'] = {
            "text": caption,
            "buttons_raw": "",
            "media_id": fid,
            "media_type": mtype
        }
        kb = [
            [InlineKeyboardButton("➕ Add Buttons", callback_data="add_buttons_multipost")],
            [InlineKeyboardButton("💾 Save Post", callback_data="save_multipost")],
            [InlineKeyboardButton("📤 Send All Posts", callback_data="send_all_multipost")],
            [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
        ]
        update.message.reply_text(
            "✅ ক্যাপশন সেভ হয়েছে! এখন বাটন যোগ করো, সেভ করো, বা সব পোস্ট পাঠাও:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        user.pop('awaiting_caption_text_multipost', None)
        user.pop('pending_file_id', None)
        user.pop('pending_type', None)
        pop_step(context)
        return

    if user.get('creating_multipost'):
        text = update.message.text or ""
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
        context.user_data['multipost_temp'] = {
            "text": main_text,
            "buttons_raw": btn_text,
            "media_id": None,
            "media_type": None
        }
        kb = [
            [InlineKeyboardButton("💾 Save Post", callback_data="save_multipost")],
            [InlineKeyboardButton("➕ Create New Post", callback_data="create_new_multipost")],
            [InlineKeyboardButton("📤 Send All Posts", callback_data="send_all_multipost")],
            [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
        ]
        update.message.reply_text(
            f"✅ পোস্ট তৈরি হয়েছে! এখন সেভ করো, নতুন পোস্ট তৈরি করো, বা সব পোস্ট পাঠাও:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if user.get('editing_post'):
        pid = user.get('editing_post')
        text = update.message.text or ""
        posts = load_json(POST_FILE)
        p = next((x for x in posts if x['id'] == pid), None)
        if not p:
            update.message.reply_text("❌ পোস্ট পাওয়া যায়নি।", reply_markup=main_menu_kb())
            user.pop('editing_post', None)
            pop_step(context)
            return
        lines = text.splitlines()
        btn_lines = []
        main_lines = []
        started_buttons = False
        for line in lines:
            if " - " in line and (("http" in line) or ("t.me" in line) or "&&" in line or "popup:" in line or "alert:" in line):
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
        pop_step(context)
        return

    if user.get('scheduling_post'):
        text = update.message.text.strip()
        pid = context.user_data.get('scheduling_post')
        dhaka_tz = pytz.timezone('Asia/Dhaka')
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
            dt = dhaka_tz.localize(dt)
            context.user_data['scheduling_time'] = dt.isoformat()
            push_step(context, 'scheduling_time', {'post_id': pid, 'datetime': dt.isoformat()})
            update.message.reply_text(
                f"নির্ধারিত সময়: {dt.strftime('%Y-%m-%d %H:%M')} (Asia/Dhaka)\n\nএখন নির্বাচন করো:\n- One Time (একবার) হলে `one_time`\n- Daily (প্রতিদিন একই সময়ে পাঠাতে) হলে `daily`",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("One Time (একবার)", callback_data="schedule_mode_one")],
                    [InlineKeyboardButton("Daily (প্রতিদিন)", callback_data="schedule_mode_daily")],
                    [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
                ])
            )
            return
        except Exception:
            pass

        try:
            dt2 = datetime.strptime(text, "%H:%M")
            today = datetime.now(dhaka_tz).replace(hour=dt2.hour, minute=dt2.minute, second=0, microsecond=0)
            context.user_data['scheduling_time'] = f"{dt2.hour:02d}:{dt2.minute:02d}"
            push_step(context, 'scheduling_time', {'post_id': pid, 'time_hm': text})
            update.message.reply_text(
                f"নির্ধারিত সময় (প্রতিদিন): {text} (Asia/Dhaka)\n\nএখন নির্বাচন করো:\n- One Time (next occurrence)\n- Daily (প্রতিদিন)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("One Time (next occurrence)", callback_data="schedule_mode_one")],
                    [InlineKeyboardButton("Daily (প্রতিদিন)", callback_data="schedule_mode_daily")],
                    [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
                ])
            )
            return
        except Exception:
            pass

        update.message.reply_text("❌ সময় বুঝতে পারছি না — দয়া করে `YYYY-MM-DD HH:MM` অথবা `HH:MM` ফরম্যাটে পাঠাও।", reply_markup=step_back_kb())
        return

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
        posts.append({"id": len(posts) + 1, "text": main_text, "buttons_raw": btn_text, "media_id": None, "media_type": None})
        save_json(POST_FILE, posts)
        update.message.reply_text("✅ পোস্ট সংরক্ষণ করা হয়েছে!", reply_markup=main_menu_kb())
        context.user_data.pop('creating_post', None)
        pop_step(context)
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
        msg.reply_text("❌ শুধু ছবি/ভিডিও/GIF পাঠাও।", reply_markup=main_menu_kb())
        return

    if context.user_data.get('creating_multipost'):
        if msg.caption:
            context.user_data['multipost_temp'] = {
                "text": msg.caption,
                "buttons_raw": "",
                "media_id": fid,
                "media_type": mtype
            }
            kb = [
                [InlineKeyboardButton("➕ Add Buttons", callback_data="add_buttons_multipost")],
                [InlineKeyboardButton("💾 Save Post", callback_data="save_multipost")],
                [InlineKeyboardButton("📤 Send All Posts", callback_data="send_all_multipost")],
                [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
            ]
            msg.reply_text(
                "✅ মিডিয়া ও ক্যাপশন সেভ হয়েছে! এখন বাটন যোগ করো, সেভ করো, বা সব পোস্ট পাঠাও:",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            push_step(context, 'multipost_media_with_caption')
            return
        context.user_data['pending_file_id'] = fid
        context.user_data['pending_type'] = mtype
        push_step(context, 'awaiting_caption_choice_multipost', {'file_id': fid, 'type': mtype})
        kb = [
            [InlineKeyboardButton("✍️ Add Caption", callback_data="add_caption_multipost")],
            [InlineKeyboardButton("⏭️ Skip (no caption)", callback_data="skip_caption_multipost")],
            [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
        ]
        msg.reply_text("📝 আপনি কি ক্যাপশন যোগ করতে চান?", reply_markup=InlineKeyboardMarkup(kb))
        return

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
        new_id = len(posts)
        kb = [
            [InlineKeyboardButton("➕ Add Buttons", callback_data=f"add_buttons_{new_id}")],
            [InlineKeyboardButton("📤 Send Post", callback_data=f"send_post_{new_id}")],
            [InlineKeyboardButton("🕒 Schedule Post", callback_data=f"schedule_post_{new_id}")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        msg.reply_text("✅ মিডিয়া ও ক্যাপশন সেভ হয়েছে! এখন চাইলে বাটন যোগ করো বা সরাসরি পাঠাও:", reply_markup=InlineKeyboardMarkup(kb))
        return

    context.user_data['pending_file_id'] = fid
    context.user_data['pending_type'] = mtype
    push_step(context, 'awaiting_caption_choice', {'file_id': fid, 'type': mtype})
    kb = [
        [InlineKeyboardButton("✍️ Add Caption", callback_data="add_caption")],
        [InlineKeyboardButton("⏭️ Skip (no caption)", callback_data="skip_caption")],
        [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
    ]
    msg.reply_text("📝 আপনি কি ক্যাপশন যোগ করতে চান?", reply_markup=InlineKeyboardMarkup(kb))

def caption_choice_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    data = q.data
    if data == "add_caption":
        q.message.reply_text("✍️ এখন ক্যাপশন লিখে পাঠান:", reply_markup=step_back_kb())
        context.user_data['awaiting_caption_text'] = True
        push_step(context, 'awaiting_caption_text', {'pending_file_id': context.user_data.get('pending_file_id')})
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
            [InlineKeyboardButton("🕒 Schedule Post", callback_data=f"schedule_post_{new_id}")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        q.message.reply_text("✅ মিডিয়া (ক্যাপশন ছাড়া) সেভ করা হয়েছে! এখন চাইলে বাটন যোগ করো বা সরাসরি পাঠাও:", reply_markup=InlineKeyboardMarkup(kb))
        context.user_data.pop('pending_file_id', None)
        context.user_data.pop('pending_type', None)
        pop_step(context)
    else:
        q.message.reply_text("❌ অজানা অপশন", reply_markup=main_menu_kb())

def caption_choice_multipost_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    data = q.data
    if data == "add_caption_multipost":
        q.message.reply_text("✍️ এখন ক্যাপশন লিখে পাঠান:", reply_markup=step_back_kb())
        context.user_data['awaiting_caption_text_multipost'] = True
        push_step(context, 'awaiting_caption_text_multipost')
    elif data == "skip_caption_multipost":
        fid = context.user_data.get('pending_file_id')
        mtype = context.user_data.get('pending_type')
        context.user_data['multipost_temp'] = {
            "text": "",
            "buttons_raw": "",
            "media_id": fid,
            "media_type": mtype
        }
        kb = [
            [InlineKeyboardButton("➕ Add Buttons", callback_data="add_buttons_multipost")],
            [InlineKeyboardButton("💾 Save Post", callback_data="save_multipost")],
            [InlineKeyboardButton("📤 Send All Posts", callback_data="send_all_multipost")],
            [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
        ]
        q.message.reply_text(
            "✅ মিডিয়া (ক্যাপশন ছাড়া) সেভ করা হয়েছে! এখন বাটন যোগ করো, সেভ করো, বা সব পোস্ট পাঠাও:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        context.user_data.pop('pending_file_id', None)
        context.user_data.pop('pending_type', None)
        pop_step(context)
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
    context.user_data['awaiting_buttons_for_post_id'] = pid
    push_step(context, 'awaiting_buttons_for_post_id', {'post_id': pid})
    q.message.reply_text(
        "✍️ এখন বাটন লাইন পাঠাও (উদাহরণ):\n`⎙ WATCH & DOWNLOAD ⎙ - https://t.me/fandub01 && 💬 GROUP - https://t.me/hindianime03`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=step_back_kb()
    )

def add_buttons_multipost_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    context.user_data['awaiting_buttons_for_multipost'] = True
    push_step(context, 'awaiting_buttons_for_multipost')
    q.message.reply_text(
        "✍️ এখন বাটন লাইন পাঠাও (উদাহরণ):\n`⎙ WATCH & DOWNLOAD ⎙ - https://t.me/fandub01 && 💬 GROUP - https://t.me/hindianime03`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=step_back_kb()
    )

def save_multipost_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    if 'multipost_temp' in context.user_data:
        posts = load_json(POST_FILE)
        temp_post = context.user_data['multipost_temp']
        # নতুন ID তৈরি
        new_id = len(posts) + 1
        temp_post['id'] = new_id
        posts.append(temp_post)
        save_json(POST_FILE, posts)
        
        # মাল্টিপোস্ট লিস্টে ID সংরক্ষণ
        if 'multipost_list' not in context.user_data:
            context.user_data['multipost_list'] = []
        context.user_data['multipost_list'].append(new_id)
        
        context.user_data.pop('multipost_temp', None)
        kb = [
            [InlineKeyboardButton("➕ Create New Post", callback_data="create_new_multipost")],
            [InlineKeyboardButton("📤 Send All Posts", callback_data="send_all_multipost")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        q.message.reply_text(
            f"✅ পোস্ট সেভ হয়েছে! মোট সেভ করা পোস্ট: {len(context.user_data['multipost_list'])}",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        q.message.reply_text("❌ কোনো পোস্ট তৈরি করা হয়নি।", reply_markup=main_menu_kb())

def create_new_multipost_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    q.message.reply_text(
        "📝 নতুন পোস্ট তৈরি শুরু করো।\n\n"
        "মিডিয়া (ছবি/ভিডিও/GIF) অথবা টেক্সট পাঠাও।",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=step_back_kb()
    )
    context.user_data.pop('multipost_temp', None)
    push_step(context, 'creating_multipost')

def send_all_multipost_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    multipost_ids = context.user_data.get('multipost_list', [])
    
    if not multipost_ids:
        q.message.reply_text("❌ কোনো পোস্ট সেভ করা হয়নি।", reply_markup=main_menu_kb())
        return
        
    count = 0
    for pid in multipost_ids:
        post = next((p for p in posts if p['id'] == pid), None)
        if post:
            sent = send_post_to_channels(context, post)
            count += sent
    
    context.user_data.pop('multipost_list', None)
    context.user_data.pop('creating_multipost', None)
    clear_steps(context)
    
    q.message.reply_text(
        f"✅ মোট {count} চ্যানেলে পোস্ট পাঠানো হয়েছে!",
        reply_markup=main_menu_kb()
    )

# -----------------------
# My posts / view / delete / edit flows
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

def menu_edit_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    
    # চ্যানেল থেকে সব পোস্ট লোড করবে
    channels = load_json(CHANNEL_FILE)
    if not channels:
        q.message.reply_text("❗ কোনো চ্যানেল নেই। Add channel দিয়ে যোগ করো।", reply_markup=back_to_menu_kb())
        return
    
    # সব পোস্ট সংগ্রহ
    all_posts = []
    for channel in channels:
        try:
            # চ্যানেল থেকে শেষ 20টি পোস্ট সংগ্রহ
            messages = context.bot.get_chat_history(chat_id=channel['id'], limit=20)
            for message in messages:
                if message.text or message.caption or message.photo or message.video:
                    post_data = {
                        'channel_id': channel['id'],
                        'channel_title': channel['title'],
                        'message_id': message.message_id,
                        'date': message.date.isoformat() if message.date else datetime.now().isoformat(),
                        'text': message.text or message.caption or '',
                        'media_type': None,
                        'media_id': None,
                        'buttons': None
                    }
                    
                    # মিডিয়া টাইপ ও আইডি সংরক্ষণ
                    if message.photo:
                        post_data['media_type'] = 'photo'
                        post_data['media_id'] = message.photo[-1].file_id
                    elif message.video:
                        post_data['media_type'] = 'video'
                        post_data['media_id'] = message.video.file_id
                    elif message.animation:
                        post_data['media_type'] = 'animation'
                        post_data['media_id'] = message.animation.file_id
                    
                    # বাটন সংরক্ষণ
                    if message.reply_markup:
                        buttons = []
                        for row in message.reply_markup.inline_keyboard:
                            button_row = []
                            for btn in row:
                                if btn.url:
                                    button_row.append(f"{btn.text} - {btn.url}")
                                elif btn.callback_data:
                                    button_row.append(f"{btn.text} - {btn.callback_data}")
                            if button_row:
                                buttons.append(" && ".join(button_row))
                        post_data['buttons'] = "\n".join(buttons)
                    
                    all_posts.append(post_data)
        except Exception as e:
            logging.error(f"চ্যানেল {channel['title']} থেকে পোস্ট লোড করতে সমস্যা: {e}")
    
    if not all_posts:
        q.message.reply_text("📭 চ্যানেলে কোনো পোস্ট পাওয়া যায়নি।", reply_markup=back_to_menu_kb())
        return
    
    # তারিখ অনুযায়ী সাজানো (নতুন থেকে পুরাতন)
    all_posts.sort(key=lambda x: x['date'], reverse=True)
    
    # কনটেক্সটে সংরক্ষণ
    context.user_data['editable_posts'] = all_posts
    context.user_data['current_edit_page'] = 0
    
    # পোস্ট লিস্ট দেখানো
    show_editable_posts(update, context)

def show_editable_posts(update: Update, context: CallbackContext):
    editable_posts = context.user_data.get('editable_posts', [])
    current_page = context.user_data.get('current_edit_page', 0)
    
    if not editable_posts:
        if update.callback_query:
            update.callback_query.message.reply_text("❌ কোনো এডিটযোগ্য পোস্ট নেই।", reply_markup=main_menu_kb())
        else:
            update.message.reply_text("❌ কোনো এডিটযোগ্য পোস্ট নেই।", reply_markup=main_menu_kb())
        return
    
    posts_per_page = 5
    start_idx = current_page * posts_per_page
    end_idx = start_idx + posts_per_page
    page_posts = editable_posts[start_idx:end_idx]
    
    text = f"✏️ **এডিটযোগ্য পোস্ট লিস্ট** (পৃষ্ঠা {current_page + 1})\n\n"
    
    kb = []
    for i, post in enumerate(page_posts):
        post_num = start_idx + i + 1
        post_date = datetime.fromisoformat(post['date']).strftime('%d-%m-%Y %H:%M')
        post_preview = post['text'][:30] + "..." if len(post['text']) > 30 else post['text']
        
        text += f"{post_num}. **{post['channel_title']}**\n"
        text += f"   📅 {post_date}\n"
        text += f"   📝 {post_preview}\n\n"
        
        kb.append([InlineKeyboardButton(
            f"✏️ এডিট {post_num} ({post['channel_title']})", 
            callback_data=f"edit_channel_post_{start_idx + i}"
        )])
    
    # পেজিনেশন বাটন
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ আগের পাতা", callback_data="edit_prev_page"))
    
    if end_idx < len(editable_posts):
        nav_buttons.append(InlineKeyboardButton("➡️ পরের পাতা", callback_data="edit_next_page"))
    
    if nav_buttons:
        kb.append(nav_buttons)
    
    kb.append([InlineKeyboardButton("↩️ মেনুতে ফিরুন", callback_data="back_to_menu")])
    
    if update.callback_query:
        update.callback_query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
    else:
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

def edit_channel_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    
    try:
        post_idx = int(q.data.split("_")[-1])
    except:
        q.message.reply_text("❌ ভুল পোস্ট ইনডেক্স")
        return
    
    editable_posts = context.user_data.get('editable_posts', [])
    if post_idx >= len(editable_posts):
        q.message.reply_text("❌ পোস্ট পাওয়া যায়নি")
        return
    
    selected_post = editable_posts[post_idx]
    
    # নির্বাচিত পোস্ট সংরক্ষণ
    context.user_data['editing_channel_post'] = selected_post
    context.user_data['editing_post_index'] = post_idx
    
    # পোস্ট প্রিভিউ দেখানো
    text = f"**পোস্ট প্রিভিউ:**\n\n{selected_post['text']}\n\n"
    if selected_post['buttons']:
        text += f"**বাটন:**\n{selected_post['buttons']}\n\n"
    
    text += "✏️ **এখন নতুন টেক্সট পাঠান:**\n(পুরো পোস্ট টেক্সট আবার লিখুন, বাটনসহ)"
    
    # বাটন অপশন
    kb = [
        [InlineKeyboardButton("📝 শুধু টেক্সট এডিট করুন", callback_data="edit_text_only")],
        [InlineKeyboardButton("↩️ পোস্ট লিস্টে ফিরুন", callback_data="back_to_edit_list")]
    ]
    
    # যদি মিডিয়া থাকে
    if selected_post['media_type']:
        try:
            if selected_post['media_type'] == 'photo':
                q.message.reply_photo(
                    photo=selected_post['media_id'],
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            elif selected_post['media_type'] == 'video':
                q.message.reply_video(
                    video=selected_post['media_id'],
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            elif selected_post['media_type'] == 'animation':
                q.message.reply_animation(
                    animation=selected_post['media_id'],
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            return
        except Exception as e:
            logging.error(f"মিডিয়া লোড করতে সমস্যা: {e}")
    
    # যদি শুধু টেক্সট হয়
    q.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

def edit_text_only_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    
    context.user_data['awaiting_edit_text'] = True
    q.message.reply_text(
        "📝 **নতুন টেক্সট লিখে পাঠান:**\n\n"
        "পুরো পোস্টের টেক্সট আবার লিখুন। বাটন যোগ করতে চাইলে নিচের ফরম্যাটে লিখুন:\n\n"
        "`পোস্টের মূল টেক্সট...\n\nবাটন ১ - https://example.com && বাটন ২ - https://example2.com`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=step_back_kb()
    )

def process_post_edit(update: Update, context: CallbackContext):
    if context.user_data.get('awaiting_edit_text'):
        new_text = update.message.text
        
        # টেক্সট ও বাটন আলাদা করা
        lines = new_text.split('\n')
        text_lines = []
        button_lines = []
        in_buttons = False
        
        for line in lines:
            if ' - http' in line or ' - https://' in line or ' - tg://' in line or ' && ' in line:
                in_buttons = True
                button_lines.append(line.strip())
            else:
                if in_buttons:
                    button_lines.append(line.strip())
                else:
                    text_lines.append(line.strip())
        
        main_text = '\n'.join(text_lines).strip()
        buttons_text = '\n'.join(button_lines).strip()
        
        # চ্যানেলে পোস্ট এডিট করা
        selected_post = context.user_data.get('editing_channel_post')
        if selected_post:
            try:
                # চ্যানেলে মেসেজ এডিট করা
                if selected_post['media_type']:
                    # মিডিয়া পোস্ট এডিট
                    context.bot.edit_message_caption(
                        chat_id=selected_post['channel_id'],
                        message_id=selected_post['message_id'],
                        caption=main_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=parse_buttons_from_text(buttons_text)
                    )
                else:
                    # টেক্সট পোস্ট এডিট
                    context.bot.edit_message_text(
                        chat_id=selected_post['channel_id'],
                        message_id=selected_post['message_id'],
                        text=main_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=parse_buttons_from_text(buttons_text)
                    )
                
                # লোকাল ডাটা আপডেট
                editable_posts = context.user_data.get('editable_posts', [])
                post_idx = context.user_data.get('editing_post_index')
                if post_idx is not None and post_idx < len(editable_posts):
                    editable_posts[post_idx]['text'] = main_text
                    editable_posts[post_idx]['buttons'] = buttons_text
                    context.user_data['editable_posts'] = editable_posts
                
                update.message.reply_text(
                    "✅ **পোস্ট সফলভাবে এডিট করা হয়েছে!**\n\n"
                    f"চ্যানেল: {selected_post['channel_title']}\n"
                    f"মেসেজ ID: {selected_post['message_id']}",
                    reply_markup=main_menu_kb()
                )
                
                # ক্লিনআপ
                context.user_data.pop('awaiting_edit_text', None)
                context.user_data.pop('editing_channel_post', None)
                context.user_data.pop('editing_post_index', None)
                
            except Exception as e:
                update.message.reply_text(
                    f"❌ **পোস্ট এডিট করতে সমস্যা:** {str(e)}\n\n"
                    "বটটির চ্যানেলে এডিট করার অনুমতি আছে কিনা চেক করুন।",
                    reply_markup=main_menu_kb()
                )
        
    elif context.user_data.get('awaiting_edit_media'):
        # মিডিয়া এডিটের লজিক এখানে যোগ করতে হবে
        update.message.reply_text("❌ মিডিয়া এডিট ফিচারটি শীঘ্রই আসছে!", reply_markup=main_menu_kb())
        context.user_data.pop('awaiting_edit_media', None)

def edit_page_navigation_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    
    data = q.data
    current_page = context.user_data.get('current_edit_page', 0)
    
    if data == 'edit_prev_page' and current_page > 0:
        context.user_data['current_edit_page'] = current_page - 1
    elif data == 'edit_next_page':
        context.user_data['current_edit_page'] = current_page + 1
    
    show_editable_posts(update, context)

def back_to_edit_list_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    
    context.user_data.pop('editing_channel_post', None)
    context.user_data.pop('editing_post_index', None)
    context.user_data.pop('awaiting_edit_text', None)
    context.user_data.pop('awaiting_edit_media', None)
    
    show_editable_posts(update, context)

def choose_edit_post_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    pid = int(q.data.split("_")[-1])
    context.user_data['editing_post'] = pid
    push_step(context, 'editing_post', {'post_id': pid})
    q.message.reply_text("✏️ নতুন টেক্সট বা বাটন লাইন পাঠাও (বাটন ফরম্যাট দেখতে Guide চাপো).", reply_markup=step_back_kb())

# -----------------------
# Multipost
# -----------------------
def menu_multipost_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    context.user_data['creating_multipost'] = True
    context.user_data['multipost_list'] = []
    push_step(context, 'creating_multipost')
    q.message.reply_text(
        "🧾 Multipost: পোস্ট তৈরি শুরু করো।\n\n"
        "📎 প্রথমে মিডিয়া (ছবি/ভিডিও/GIF) পাঠাও অথবা সরাসরি টেক্সট পাঠাও।\n"
        "মিডিয়া পাঠালে ক্যাপশন যোগ করার অপশন পাবে, তারপর বাটন যোগ করতে পারবে।",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=step_back_kb()
    )

# -----------------------
# Send helpers
# -----------------------
def send_post_to_channels(context: CallbackContext, post: dict):
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
            logging.exception("Send Error to channel %s", ch.get('id'))
    return sent

# -----------------------
# Send post
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

    sent = send_post_to_channels(context, post)
    q.message.reply_text(f"✅ পোস্ট {sent} চ্যানেলে পাঠানো হয়েছে।", reply_markup=main_menu_kb())

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
    sent = send_post_to_channels(context, post)
    q.message.reply_text(f"✅ পোস্ট {sent} চ্যানেলে পাঠানো হয়েছে!", reply_markup=main_menu_kb())

def multipost_send_all_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    count = 0
    for p in posts:
        sent = send_post_to_channels(context, p)
        if sent:
            count += 1
    q.message.reply_text(f"✅ সমস্ত পোস্ট আজ সব চ্যানেলে পাঠানো হয়েছে (প্রতিটি পোস্টকে আলাদাভাবে পাঠানোর চেষ্টা করা হয়েছে)।", reply_markup=main_menu_kb())

# -----------------------
# Button guide and generic callbacks
# -----------------------
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
    clear_steps(context)
    q.message.reply_text("↩️ মূল মেনুতে ফিরে আসা হলো", reply_markup=main_menu_kb())

# -----------------------
# Step-back
# -----------------------
def step_back_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    current = pop_step(context)
    prev = peek_prev_step(context)
    if current:
        name = current.get('name')
        if name == 'awaiting_caption_text':
            context.user_data.pop('awaiting_caption_text', None)
            context.user_data.pop('pending_file_id', None)
            context.user_data.pop('pending_type', None)
        elif name == 'awaiting_buttons_for_post_id':
            context.user_data.pop('awaiting_buttons_for_post_id', None)
        elif name == 'creating_multipost':
            context.user_data.pop('creating_multipost', None)
            context.user_data.pop('multipost_temp', None)
            context.user_data.pop('multipost_list', None)
        elif name == 'editing_post':
            context.user_data.pop('editing_post', None)
        elif name == 'expecting_forward_for_add':
            context.user_data.pop('expecting_forward_for_add', None)
        elif name == 'awaiting_caption_text_multipost':
            context.user_data.pop('awaiting_caption_text_multipost', None)
            context.user_data.pop('pending_file_id', None)
            context.user_data.pop('pending_type', None)
        elif name == 'awaiting_buttons_for_multipost':
            context.user_data.pop('awaiting_buttons_for_multipost', None)
        elif name == 'awaiting_caption_choice_multipost':
            context.user_data.pop('pending_file_id', None)
            context.user_data.pop('pending_type', None)

    if not prev:
        q.message.reply_text("↩️ আর কোন পূর্বের ধাপ নেই — মূল মেনুতে ফিরে গেলাম।", reply_markup=main_menu_kb())
        clear_steps(context)
        return

    pname = prev.get('name')
    info = prev.get('info', {})
    if pname == 'creating_post':
        q.message.reply_text("📝 তুমি পোস্ট তৈরিতে আছ — মিডিয়া পাঠাও বা টেক্সট লিখে পাঠাও।", reply_markup=step_back_kb())
    elif pname == 'awaiting_caption_choice':
        q.message.reply_text("📝 আপনি কি ক্যাপশন যোগ করতে চান?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Add Caption", callback_data="add_caption")],
            [InlineKeyboardButton("⏭️ Skip (no caption)", callback_data="skip_caption")],
            [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
        ]))
    elif pname == 'awaiting_caption_text':
        q.message.reply_text("✍️ এখন ক্যাপশন লিখে পাঠান:", reply_markup=step_back_kb())
    elif pname == 'awaiting_buttons_for_post_id':
        pid = info.get('post_id')
        q.message.reply_text(f"✍️ এখন বাটন লাইন পাঠাও (পোস্ট আইডি: {pid})", reply_markup=step_back_kb())
    elif pname == 'creating_multipost':
        q.message.reply_text(
            "📝 নতুন পোস্ট তৈরি শুরু করো।\n\n"
            "মিডিয়া (ছবি/ভিডিও/GIF) অথবা টেক্সট পাঠাও।",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=step_back_kb()
        )
    elif pname == 'awaiting_caption_choice_multipost':
        q.message.reply_text("📝 আপনি কি ক্যাপশন যোগ করতে চান?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Add Caption", callback_data="add_caption_multipost")],
            [InlineKeyboardButton("⏭️ Skip (no caption)", callback_data="skip_caption_multipost")],
            [InlineKeyboardButton("↩️ Back (one step)", callback_data="step_back")]
        ]))
    elif pname == 'awaiting_caption_text_multipost':
        q.message.reply_text("✍️ এখন ক্যাপশন লিখে পাঠান:", reply_markup=step_back_kb())
    elif pname == 'awaiting_buttons_for_multipost':
        q.message.reply_text(
            "✍️ এখন বাটন লাইন পাঠাও (উদাহরণ):\n`⎙ WATCH & DOWNLOAD ⎙ - https://t.me/fandub01 && 💬 GROUP - https://t.me/hindianime03`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=step_back_kb()
        )
    elif pname == 'editing_post':
        pid = info.get('post_id')
        q.message.reply_text(f"✏️ নতুন টেক্সট বা বাটন লাইন পাঠাও (Edit Post {pid})", reply_markup=step_back_kb())
    elif pname == 'scheduling_post':
        pid = info.get('post_id')
        q.message.reply_text(
            "🕒 সময় লিখে পাঠাও (Asia/Dhaka date/time বা মাত্র সময়):\n\n"
            "Format examples:\n"
            "`2025-10-06 15:30`  (one-time — Asia/Dhaka)\n"
            "`15:30`  (daily at 15:30 Asia/Dhaka)\n\n"
            "এর পরে বট তোমাকে জিজ্ঞাসা করবে One-time না Daily বোঝার জন্য।",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=step_back_kb()
        )
    else:
        q.message.reply_text("↩️ পূর্বের ধাপে ফিরে এলাম।", reply_markup=main_menu_kb())

# -----------------------
# Delete flows
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
# Scheduling
# -----------------------
def menu_schedule_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    posts = load_json(POST_FILE)
    if not posts:
        q.message.reply_text("❗ কোনো পোস্ট নেই — আগে পোস্ট তৈরি করো।", reply_markup=back_to_menu_kb())
        return
    kb = []
    for p in posts:
        kb.append([InlineKeyboardButton(f"🕒 Schedule Post {p['id']}", callback_data=f"schedule_post_{p['id']}")])
    kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    q.message.reply_text("কোন পোস্ট শিডিউল করতে চাও?", reply_markup=InlineKeyboardMarkup(kb))

def schedule_post_cb(update: Update, context: CallbackContext):
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
    context.user_data['scheduling_post'] = pid
    push_step(context, 'scheduling_post', {'post_id': pid})
    q.message.reply_text(
        "🕒 সময় লিখে পাঠাও (Asia/Dhaka date/time বা মাত্র সময়):\n\n"
        "Format examples:\n"
        "`2025-10-06 15:30`  (one-time — Asia/Dhaka)\n"
        "`15:30`  (daily at 15:30 Asia/Dhaka)\n\n"
        "এর পরে বট তোমাকে জিজ্ঞাসা করবে One-time না Daily বোঝার জন্য।",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=step_back_kb()
    )

def schedule_mode_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    mode = q.data
    pid = context.user_data.get('scheduling_post')
    if not pid:
        q.message.reply_text("❌ পোস্ট আইডি পাওয়া যায়নি।", reply_markup=main_menu_kb())
        return
    stime = context.user_data.get('scheduling_time')
    if not stime:
        q.message.reply_text("❌ সময় পাওয়া যায়নি।", reply_markup=main_menu_kb())
        return

    dhaka_tz = pytz.timezone('Asia/Dhaka')
    scheduled = load_json(SCHEDULE_FILE)
    if mode == 'schedule_mode_one':
        try:
            if len(stime) == 5 and ":" in stime:
                hh, mm = map(int, stime.split(":"))
                now = datetime.now(dhaka_tz)
                candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if candidate <= now:
                    candidate += timedelta(days=1)
                run_at = candidate.isoformat()
            else:
                run_at = stime
        except Exception:
            run_at = stime
        item = {
            "id": len(scheduled) + 1,
            "post_id": pid,
            "mode": "one",
            "run_at": run_at
        }
        scheduled.append(item)
        save_json(SCHEDULE_FILE, scheduled)
        q.message.reply_text(f"✅ One-time schedule set for post {pid} at {run_at} (Asia/Dhaka).", reply_markup=main_menu_kb())
        context.user_data.pop('scheduling_post', None)
        context.user_data.pop('scheduling_time', None)
        pop_step(context)
        pop_step(context)
        return
    elif mode == 'schedule_mode_daily':
        if len(stime) == 5 and ":" in stime:
            hhmm = stime
        else:
            try:
                dt = datetime.fromisoformat(stime).astimezone(dhaka_tz)
                hhmm = f"{dt.hour:02d}:{dt.minute:02d}"
            except Exception:
                hhmm = stime
        item = {
            "id": len(scheduled) + 1,
            "post_id": pid,
            "mode": "daily",
            "time_hm": hhmm
        }
        scheduled.append(item)
        save_json(SCHEDULE_FILE, scheduled)
        q.message.reply_text(f"✅ Daily schedule set for post {pid} at {hhmm} (Asia/Dhaka, every day).", reply_markup=main_menu_kb())
        context.user_data.pop('scheduling_post', None)
        context.user_data.pop('scheduling_time', None)
        pop_step(context)
        pop_step(context)
        return
    else:
        q.message.reply_text("❌ Unknown scheduling mode.", reply_markup=main_menu_kb())

# -----------------------
# Schedule Management
# -----------------------
def menu_schedule_manage_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    
    scheduled = load_json(SCHEDULE_FILE)
    if not scheduled:
        q.message.reply_text("📭 কোনো শিডিউল করা পোস্ট নেই।", reply_markup=back_to_menu_kb())
        return
    
    dhaka_tz = pytz.timezone('Asia/Dhaka')
    now = datetime.now(dhaka_tz)
    
    text = "⏰ **শিডিউল করা পোস্ট:**\n\n"
    kb = []
    
    for item in scheduled:
        posts = load_json(POST_FILE)
        post = next((p for p in posts if p['id'] == item['post_id']), None)
        post_title = f"Post {item['post_id']}" if post else "অজানা পোস্ট"
        
        if item.get('mode') == 'one' and 'run_at' in item:
            try:
                run_dt = datetime.fromisoformat(item['run_at']).astimezone(dhaka_tz)
                time_left = run_dt - now
                if time_left.total_seconds() > 0:
                    days = time_left.days
                    hours = time_left.seconds // 3600
                    minutes = (time_left.seconds % 3600) // 60
                    time_str = f"{days}দিন {hours}ঘণ্টা {minutes}মিনিট"
                    text += f"📅 {post_title} - {run_dt.strftime('%Y-%m-%d %H:%M')}\n⏳ বাকি: {time_str}\n\n"
                else:
                    text += f"✅ {post_title} - পাঠানো হবে শীঘ্রই\n\n"
            except:
                text += f"❌ {post_title} - সময় ফরম্যাট ত্রুটি\n\n"
                
        elif item.get('mode') == 'daily' and 'time_hm' in item:
            text += f"🔄 {post_title} - প্রতিদিন {item['time_hm']} টায়\n\n"
        
        kb.append([InlineKeyboardButton(f"🗑 Delete {post_title}", callback_data=f"delete_schedule_{item['id']}")])
    
    kb.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    
    q.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

def delete_schedule_cb(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    
    try:
        schedule_id = int(q.data.split("_")[-1])
    except:
        q.message.reply_text("❌ ভুল শিডিউল আইডি")
        return
    
    scheduled = load_json(SCHEDULE_FILE)
    scheduled = [s for s in scheduled if s['id'] != schedule_id]
    save_json(SCHEDULE_FILE, scheduled)
    
    q.message.reply_text("✅ শিডিউল ডিলিট করা হয়েছে!", reply_markup=main_menu_kb())

# -----------------------
# উন্নত শিডিউলার থ্রেড
# -----------------------
def scheduler_loop(updater_dispatcher):
    logging.info("✅ উন্নত শিডিউলার শুরু হয়েছে!")
    dhaka_tz = pytz.timezone('Asia/Dhaka')
    
    while True:
        try:
            scheduled = load_json(SCHEDULE_FILE)
            if scheduled:
                now = datetime.now(dhaka_tz)
                to_remove = []
                
                for item in scheduled:
                    try:
                        # One-time শিডিউল
                        if item.get('mode') == 'one' and 'run_at' in item:
                            run_dt = datetime.fromisoformat(item['run_at']).astimezone(dhaka_tz)
                            
                            # কাউন্টডাউন লগ
                            time_left = run_dt - now
                            if time_left.total_seconds() <= 60:  # 1 মিনিটের মধ্যে
                                logging.info(f"⏰ শিডিউল করা পোস্ট {item['post_id']} পাঠানো হবে {time_left.total_seconds()} সেকেন্ডে")
                            
                            if now >= run_dt:
                                posts = load_json(POST_FILE)
                                post = next((p for p in posts if p['id'] == item['post_id']), None)
                                if post:
                                    logging.info(f"✅ শিডিউলার এককালীন পোস্ট পাঠাচ্ছে: {item['post_id']}")
                                    channels = load_json(CHANNEL_FILE)
                                    sent_count = 0
                                    
                                    for ch in channels:
                                        try:
                                            markup = parse_buttons_from_text(post.get('buttons_raw', ''))
                                            caption = post.get("text", "")
                                            
                                            if post.get("media_type") == "photo":
                                                updater_dispatcher.bot.send_photo(
                                                    chat_id=ch['id'], 
                                                    photo=post["media_id"], 
                                                    caption=caption or None, 
                                                    parse_mode=ParseMode.MARKDOWN, 
                                                    reply_markup=markup
                                                )
                                            elif post.get("media_type") == "video":
                                                updater_dispatcher.bot.send_video(
                                                    chat_id=ch['id'], 
                                                    video=post["media_id"], 
                                                    caption=caption or None, 
                                                    parse_mode=ParseMode.MARKDOWN, 
                                                    reply_markup=markup
                                                )
                                            elif post.get("media_type") == "animation":
                                                updater_dispatcher.bot.send_animation(
                                                    chat_id=ch['id'], 
                                                    animation=post["media_id"], 
                                                    caption=caption or None, 
                                                    parse_mode=ParseMode.MARKDOWN, 
                                                    reply_markup=markup
                                                )
                                            else:
                                                updater_dispatcher.bot.send_message(
                                                    chat_id=ch['id'], 
                                                    text=caption or "(No text)", 
                                                    parse_mode=ParseMode.MARKDOWN, 
                                                    reply_markup=markup
                                                )
                                            sent_count += 1
                                            time.sleep(1)  # স্প্যাম প্রতিরোধ
                                            
                                        except Exception as e:
                                            logging.error(f"❌ চ্যানেলে পাঠানো যায়নি {ch['id']}: {e}")
                                    
                                    logging.info(f"✅ {sent_count} চ্যানেলে পোস্ট পাঠানো হয়েছে")
                                    to_remove.append(item)
                                    
                                else:
                                    logging.error(f"❌ পোস্ট পাওয়া যায়নি: {item['post_id']}")
                                    to_remove.append(item)
                        
                        # Daily শিডিউল
                        elif item.get('mode') == 'daily' and 'time_hm' in item:
                            hhmm = item['time_hm']
                            hh, mm = map(int, hhmm.split(":"))
                            
                            # প্রতিদিন নির্দিষ্ট সময়ে পাঠানো
                            if now.hour == hh and now.minute == mm:
                                last_sent = item.get('last_sent')
                                can_send = False
                                
                                if not last_sent:
                                    can_send = True
                                else:
                                    try:
                                        last_dt = datetime.fromisoformat(last_sent).astimezone(dhaka_tz)
                                        # কমপক্ষে 23 ঘন্টা পরেই আবার পাঠানো যাবে
                                        if (now - last_dt) > timedelta(hours=23):
                                            can_send = True
                                    except:
                                        can_send = True
                                
                                if can_send:
                                    posts = load_json(POST_FILE)
                                    post = next((p for p in posts if p['id'] == item['post_id']), None)
                                    if post:
                                        logging.info(f"✅ শিডিউলার দৈনিক পোস্ট পাঠাচ্ছে: {item['post_id']}")
                                        channels = load_json(CHANNEL_FILE)
                                        sent_count = 0
                                        
                                        for ch in channels:
                                            try:
                                                markup = parse_buttons_from_text(post.get('buttons_raw', ''))
                                                caption = post.get("text", "")
                                                
                                                if post.get("media_type") == "photo":
                                                    updater_dispatcher.bot.send_photo(
                                                        chat_id=ch['id'], 
                                                        photo=post["media_id"], 
                                                        caption=caption or None, 
                                                        parse_mode=ParseMode.MARKDOWN, 
                                                        reply_markup=markup
                                                    )
                                                elif post.get("media_type") == "video":
                                                    updater_dispatcher.bot.send_video(
                                                        chat_id=ch['id'], 
                                                        video=post["media_id"], 
                                                        caption=caption or None, 
                                                        parse_mode=ParseMode.MARKDOWN, 
                                                        reply_markup=markup
                                                    )
                                                elif post.get("media_type") == "animation":
                                                    updater_dispatcher.bot.send_animation(
                                                        chat_id=ch['id'], 
                                                        animation=post["media_id"], 
                                                        caption=caption or None, 
                                                        parse_mode=ParseMode.MARKDOWN, 
                                                        reply_markup=markup
                                                    )
                                                else:
                                                    updater_dispatcher.bot.send_message(
                                                        chat_id=ch['id'], 
                                                        text=caption or "(No text)", 
                                                        parse_mode=ParseMode.MARKDOWN, 
                                                        reply_markup=markup
                                                    )
                                                sent_count += 1
                                                time.sleep(1)
                                                
                                            except Exception as e:
                                                logging.error(f"❌ চ্যানেলে পাঠানো যায়নি {ch['id']}: {e}")
                                        
                                        item['last_sent'] = now.isoformat()
                                        save_json(SCHEDULE_FILE, scheduled)
                                        logging.info(f"✅ {sent_count} চ্যানেলে দৈনিক পোস্ট পাঠানো হয়েছে")
                                        
                    except Exception as e:
                        logging.error(f"❌ শিডিউল আইটেম প্রসেসিং ত্রুটি: {e}")
                
                # সম্পন্ন শিডিউল মুছে ফেলা
                if to_remove:
                    scheduled = [s for s in scheduled if s not in to_remove]
                    save_json(SCHEDULE_FILE, scheduled)
                    logging.info(f"✅ {len(to_remove)}টি সম্পন্ন শিডিউল মুছে ফেলা হয়েছে")
            
            time.sleep(30)  # 30 সেকেন্ড পর পর চেক
            
        except Exception as e:
            logging.error(f"❌ শিডিউলার লুপ ত্রুটি: {e}")
            time.sleep(60)

# -----------------------
# Handler registration
# -----------------------
def register_handlers(dp):
    dp.add_handler(CommandHandler("start", start))
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
    dp.add_handler(CallbackQueryHandler(menu_schedule_cb, pattern="^menu_schedule$"))
    dp.add_handler(CallbackQueryHandler(menu_schedule_manage_cb, pattern="^menu_schedule_manage$"))
    dp.add_handler(CallbackQueryHandler(back_to_menu_cb, pattern="^back_to_menu$"))
    dp.add_handler(CallbackQueryHandler(view_channel_cb, pattern=r"^view_channel_"))
    dp.add_handler(CallbackQueryHandler(remove_channel_cb, pattern=r"^remove_channel_"))
    dp.add_handler(CallbackQueryHandler(view_post_cb, pattern=r"^view_post_"))
    dp.add_handler(CallbackQueryHandler(del_post_cb, pattern=r"^del_post_"))
    dp.add_handler(CallbackQueryHandler(choose_edit_post_cb, pattern=r"^edit_post_"))
    dp.add_handler(CallbackQueryHandler(send_post_selected, pattern=r"^send_post_"))
    dp.add_handler(CallbackQueryHandler(choose_all_cb, pattern=r"^choose_all_"))
    dp.add_handler(CallbackQueryHandler(add_buttons_cb, pattern=r"^add_buttons_"))
    dp.add_handler(CallbackQueryHandler(caption_choice_cb, pattern=r"^(add_caption|skip_caption)$"))
    dp.add_handler(CallbackQueryHandler(start_delete_post_cb, pattern=r"^start_delete_post$"))
    dp.add_handler(CallbackQueryHandler(start_delete_channel_cb, pattern=r"^start_delete_channel$"))
    dp.add_handler(CallbackQueryHandler(generic_callback_cb, pattern=r"^(popup:|alert:|noop)"))
    dp.add_handler(CallbackQueryHandler(multipost_send_all_cb, pattern="^multipost_send_all$"))
    dp.add_handler(CallbackQueryHandler(schedule_post_cb, pattern=r"^schedule_post_"))
    dp.add_handler(CallbackQueryHandler(schedule_mode_cb, pattern=r"^schedule_mode_"))
    dp.add_handler(CallbackQueryHandler(step_back_cb, pattern=r"^step_back$"))
    dp.add_handler(CallbackQueryHandler(caption_choice_multipost_cb, pattern=r"^(add_caption_multipost|skip_caption_multipost)$"))
    dp.add_handler(CallbackQueryHandler(add_buttons_multipost_cb, pattern="^add_buttons_multipost$"))
    dp.add_handler(CallbackQueryHandler(save_multipost_cb, pattern="^save_multipost$"))
    dp.add_handler(CallbackQueryHandler(create_new_multipost_cb, pattern="^create_new_multipost$"))
    dp.add_handler(CallbackQueryHandler(send_all_multipost_cb, pattern="^send_all_multipost$"))
    dp.add_handler(CallbackQueryHandler(edit_channel_post_cb, pattern=r"^edit_channel_post_"))
    dp.add_handler(CallbackQueryHandler(edit_text_only_cb, pattern="^edit_text_only$"))
    dp.add_handler(CallbackQueryHandler(edit_page_navigation_cb, pattern=r"^(edit_prev_page|edit_next_page)$"))
    dp.add_handler(CallbackQueryHandler(back_to_edit_list_cb, pattern="^back_to_edit_list$"))
    dp.add_handler(CallbackQueryHandler(delete_schedule_cb, pattern=r"^delete_schedule_"))
    dp.add_handler(MessageHandler(Filters.forwarded & Filters.chat_type.private, forward_handler))
    dp.add_handler(MessageHandler(Filters.photo | Filters.video | Filters.animation, media_handler))
    dp.add_handler(MessageHandler(Filters.text & Filters.chat_type.private, save_text_handler))
    dp.add_handler(MessageHandler(Filters.text & Filters.chat_type.private & ~Filters.command, process_post_edit))

# -----------------------
# Main
# -----------------------
def main():
    ensure_files()
    if not TOKEN:
        print("ERROR: BOT_TOKEN environment variable not set. Exiting.")
        return

    try:
        updater = Updater(TOKEN, use_context=True)
        dp = updater.dispatcher
        register_handlers(dp)
        print("✅ Bot started successfully!")
        updater.start_polling()
        sched_thread = threading.Thread(target=scheduler_loop, args=(updater,), daemon=True)
        sched_thread.start()
        updater.idle()
    except Exception as e:
        print(f"❌ Bot startup failed: {e}")
        raise

# -----------------------
# Flask keep-alive
# -----------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Telegram MultiPost Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    main()
