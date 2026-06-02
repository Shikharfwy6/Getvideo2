import logging
import asyncio
import sys
import traceback
import math
import secrets
import string
import requests
import os
from datetime import datetime, timedelta
from pymongo import MongoClient
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, CommandHandler, filters, ContextTypes

# --- CRASH-PROOF LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout
)

# --- CONFIGURATIONS FROM ENV ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
BOT_USERNAME = "Getvideo81827_bot" 

if not BOT_TOKEN or not MONGO_URI:
    print("💥 Critical Error: BOT_TOKEN ya MONGO_URI missing hai!", flush=True)
    sys.exit(1)

CHANNELS = {
    "1": "-1003952628014",
    "2": "-1003758252316",
    "3": "-1003307449853",
    "4": "-1003195006898",
    "5": "-1003307449853"
}

SHORTENERS = {
    "arolinks": "https://arolinks.com/api?api=f4617908b561110a219cd2b65bc255c2c2c6ff8a&url={url}",
    "vplink": "https://vplink.in/api?api=017ab25e4402465d00047e8e2897f3c6b38afbd9&url={url}",
    "instantlinks": "https://instantlinks.co/api?api=323c4585c0d0b8bc04a170cd57a2e6a74ac6d8aa&url={url}"
}
API_ORDER = ["arolinks", "vplink", "instantlinks"]

# --- MONGODB SETUP ---
try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client["cluster_bot_db"]
    users_col = db["verified_users"]
    print("✅ MongoDB Connected Successfully!", flush=True)
except Exception as e:
    print(f"💥 MongoDB Connection Error: {e}", flush=True)
    sys.exit(1)

USER_STATES = {}
app = Flask(__name__)

# --- PTB APPLICATION SETUP ---
ptb_app = Application.builder().token(BOT_TOKEN).build()

# --- HELPER FUNCTIONS ---
def generate_random_token(length=12):
    letters_and_digits = string.ascii_lowercase + string.digits
    return "v_" + ''.join(secrets.choice(letters_and_digits) for _ in range(length))

def get_short_link(api_name, long_url):
    try:
        api_url = SHORTENERS[api_name].format(url=long_url)
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            res_text = response.text.strip()
            if "https://" in res_text or "http://" in res_text:
                return res_text
            res_json = response.json()
            return res_json.get("shortenedUrl", long_url)
    except Exception as e:
        print(f"❌ Shortener Error ({api_name}): {e}", flush=True)
    return long_url

def check_user_verification(user_id):
    user = users_col.find_one({"_id": user_id})
    now = datetime.utcnow()
    if user:
        if user.get("status") == "verified" and user.get("expire_at") > now:
            return True, user.get("current_api")
        else:
            users_col.update_one({"_id": user_id}, {"$set": {"status": "unverified"}})
            return False, user.get("current_api", "arolinks")
    return False, "arolinks"

def update_user_to_verified(user_id):
    now = datetime.utcnow()
    expire_time = now + timedelta(hours=8)
    user = users_col.find_one({"_id": user_id})
    current_api = user.get("current_api", "arolinks") if user else "arolinks"
    try:
        next_idx = (API_ORDER.index(current_api) + 1) % len(API_ORDER)
        next_api = API_ORDER[next_idx]
    except:
        next_api = "arolinks"

    users_col.update_one(
        {"_id": user_id},
        {"$set": {"status": "verified", "expire_at": expire_time, "current_api": next_api, "token": None}},
        upsert=True
    )

# --- TEXT MESSAGES HANDLER ---
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    bot = context.bot
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    text_message = update.message.text.strip()
    
    try:
        if text_message.startswith("/start"):
            parts = text_message.split()
            raw_arg = parts[1] if len(parts) > 1 else ""
            
            if raw_arg.startswith("v_"):
                user_record = users_col.find_one({"_id": user_id, "token": raw_arg})
                if user_record:
                    update_user_to_verified(user_id)
                    await bot.send_message(
                        chat_id=chat_id,
                        text="✅ **Verification Successful!**\n\nAap agle **8 Ghante** ke liye verified hain. Ab aap kisi bhi video link par click karke access kar sakte hain! 🎉"
                    )
                else:
                    await bot.send_message(chat_id=chat_id, text="❌ Invalid ya Expired verification link! Kripya fir se koshish karein.")
                return

            is_verified, next_api = check_user_verification(user_id)
            if not is_verified:
                unique_token = generate_random_token()
                users_col.update_one(
                    {"_id": user_id},
                    {"$set": {"token": unique_token, "status": "unverified", "current_api": next_api}},
                    upsert=True
                )
                destination_url = f"https://t.me/{BOT_USERNAME}?start={unique_token}"
                shortlink = get_short_link(next_api, destination_url)
                
                keyboard = [[InlineKeyboardButton("🔐 Verify Here", url=shortlink)]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ **Access Denied!**\n\nAapko aage badhne ke liye verify karna hoga. Yeh verification **8 Ghante** ke liye valid rahega.\n\n👉 **Network Used:** `{next_api.upper()}`",
                    reply_markup=reply_markup
                )
                return

            extracted_args = raw_arg.split('_') if "_" in raw_arg else [raw_arg]
            if len(extracted_args) == 4:
                start_id, end_id, ch_num, total_parts = map(int, extracted_args)
                video_list = list(range(start_id, end_id + 1))
                target_ch = CHANNELS.get(str(ch_num))
                
                if not target_ch:
                    await bot.send_message(chat_id=chat_id, text=f"❌ Configuration Error: Channel {ch_num} galat hai!")
                    return
                
                total_videos = len(video_list)
                videos_per_part = math.ceil(total_videos / total_parts)
                
                USER_STATES[user_id] = {
                    "video_list": video_list,
                    "target_ch": target_ch,
                    "videos_per_part": videos_per_part,
                    "current_part": 1,
                    "total_parts": total_parts,
                    "total_videos": total_videos
                }
                await bot.send_message(chat_id=chat_id, text=f"📊 **Verification Valid!**\nTotal Videos: `{total_videos}`\nTotal Parts: `{total_parts}`\n\n📦 **Part 1 shuru ho raha hai...**")
                await send_video_batch(chat_id, bot, user_id)
                
            elif len(extracted_args) == 2:
                file_id, ch_num = extracted_args
                target_ch = CHANNELS.get(str(ch_num))
                if target_ch:
                    await bot.copy_message(chat_id=chat_id, from_chat_id=target_ch, message_id=int(file_id))
            else:
                await bot.send_message(chat_id=chat_id, text="👋 **Welcome!**\n\nVideos paane ke liye kisi video link par click karke aao!")
            return
    except Exception as err:
        print(f"❌ Error in text handler: {err}", flush=True)

# --- BUTTON CLICK (CALLBACK QUERY) HANDLER ---
async def handle_button_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    await query.answer()
    
    is_verified, _ = check_user_verification(user_id)
    if not is_verified:
        await query.message.reply_text("⏰ Aapka 8 ghante ka session khatam ho gaya! Kripya dobara verify karne ke liye /start likhein.")
        return

    try:
        if query.data == "get_next_part":
            if user_id not in USER_STATES:
                await query.message.reply_text("❌ Session expired! Kripya video link par dobara click karein.")
                return
            state = USER_STATES[user_id]
            current_part = state["current_part"] + 1
            if current_part > state["total_parts"]:
                await query.message.reply_text("🎉 Aapke saare Parts complete ho chuke hain!")
                del USER_STATES[user_id]
                return
            state["current_part"] = current_part
            await query.message.reply_text(f"📦 **Part {current_part} shuru ho raha hai...**")
            await send_video_batch(chat_id, context.bot, user_id)
    except Exception as err:
        print(f"❌ Error in button handler: {err}", flush=True)

# --- BATCH SENDING LOGIC ---
async def send_video_batch(chat_id, bot, user_id):
    state = USER_STATES[user_id]
    video_list = state["video_list"]
    target_ch = state["target_ch"]
    current_part = state["current_part"]
    videos_per_part = state["videos_per_part"]
    total_parts = state["total_parts"]
    
    start_idx = (current_part - 1) * videos_per_part
    end_idx = start_idx + videos_per_part
    current_batch = video_list[start_idx:end_idx]
    
    if not current_batch:
        await bot.send_message(chat_id=chat_id, text="🎉 Saari videos khatam ho chuki hain!")
        del USER_STATES[user_id]
        return

    for msg_id in current_batch:
        try:
            await bot.copy_message(chat_id=chat_id, from_chat_id=target_ch, message_id=msg_id)
            await asyncio.sleep(0.5) 
        except Exception as copy_err:
            print(f"❌ Msg {msg_id} failed: {copy_err}", flush=True)
            
    next_part_num = current_part + 1
    if next_part_num <= total_parts and end_idx < len(video_list):
        keyboard = [[InlineKeyboardButton(f"➡️ Get Part {next_part_num}", callback_data="get_next_part")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await bot.send_message(
            chat_id=chat_id,
            text=f"⏸️ **Part {current_part} complete ho gaya hai ({len(current_batch)} Videos sent)!**\n\nAage ki videos (Part {next_part_num}) paane ke liye niche button par click karein 👇",
            reply_markup=reply_markup
        )
    else:
        await bot.send_message(chat_id=chat_id, text=f"🎉 **SARA VIDEO COMPLETE HO GAYA!**\n\nSabhie {total_parts} parts kamyabi se bhej diye gaye hain. ✅")
        if user_id in USER_STATES:
            del USER_STATES[user_id]

# --- INITIALIZE HANDLERS ---
ptb_app.add_handler(CommandHandler("start", handle_text_messages))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
ptb_app.add_handler(CallbackQueryHandler(handle_button_clicks))

# --- SERVERLESS-OPTIMIZED WEBHOOK PROCESSING ---
async def process_telegram_update(update_json):
    """Event Loop initialize karke task processing safe banata hai"""
    async with ptb_app:
        update = Update.de_json(update_json, ptb_app.bot)
        await ptb_app.process_update(update)

@app.route('/', methods=['GET'])
def index():
    return "Bot is alive via Serverless Webhooks!"

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == "POST":
        try:
            update_json = request.get_json(force=True)
            
            # Har request ke liye safe event loop context execution
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(process_telegram_update(update_json))
            loop.close()
            
            return jsonify({"status": "success"}), 200
        except Exception as e:
            print(f"💥 Webhook Handler Error: {e}")
            traceback.print_exc()
            return jsonify({"status": "error", "message": str(e)}), 500
    return "Invalid Method", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
