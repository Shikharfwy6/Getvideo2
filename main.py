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
VERCEL_URL = os.getenv("VERCEL_URL") # Vercel automatically provides this, or set it in Env

if not BOT_TOKEN or not MONGO_URI:
    print("💥 Critical Error: BOT_TOKEN ya MONGO_URI missing hai!", flush=True)
    sys.exit(1)

# ✅ 5 Channels Supported
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
    states_col = db["bot_states"]  # ✅ State loss se bachne ke liye naya collection
    print("✅ MongoDB Connected Successfully!", flush=True)
except Exception as e:
    print(f"💥 MongoDB Connection Error: {e}", flush=True)
    sys.exit(1)

app = Flask(__name__)
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
            return response.json().get("shortenedUrl", long_url)
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

def trigger_background_loop(user_id):
    """Vercel Timeout se bachne ke liye bot khud ko hi dubara trigger karta hai background me"""
    if VERCEL_URL:
        url = f"https://{VERCEL_URL}/background-task"
        try:
            requests.post(url, json={"user_id": user_id}, timeout=1)
        except requests.exceptions.ReadTimeout:
            pass # Timeout intentional hai taaki request asynchronusly chalti rahe
        except Exception as e:
            print(f"⚠️ Background trigger failed: {e}")

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
                    await bot.send_message(chat_id=chat_id, text="✅ **Verification Successful!**\nAap agle **8 Ghante** ke liye verified hain. 🎉")
                else:
                    await bot.send_message(chat_id=chat_id, text="❌ Invalid ya Expired verification link!")
                return

            is_verified, next_api = check_user_verification(user_id)
            if not is_verified:
                unique_token = generate_random_token()
                users_col.update_one({"_id": user_id}, {"$set": {"token": unique_token, "status": "unverified", "current_api": next_api}}, upsert=True)
                destination_url = f"https://t.me/{BOT_USERNAME}?start={unique_token}"
                shortlink = get_short_link(next_api, destination_url)
                
                keyboard = [[InlineKeyboardButton("🔐 Verify Here", url=shortlink)]]
                await bot.send_message(chat_id=chat_id, text=f"⚠️ **Access Denied!**\nVerify karein (8 Hours Validity).\n👉 **Network:** `{next_api.upper()}`", reply_markup=InlineKeyboardMarkup(keyboard))
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
                
                # ✅ Database me State save karo (Permanent)
                states_col.update_one(
                    {"_id": user_id},
                    {"$set": {
                        "chat_id": chat_id,
                        "video_list": video_list,
                        "target_ch": target_ch,
                        "videos_per_part": videos_per_part,
                        "current_part": 1,
                        "total_parts": total_parts,
                        "sent_in_current_part": 0
                    }},
                    upsert=True
                )
                await bot.send_message(chat_id=chat_id, text=f"📊 **Verification Valid!**\nTotal Videos: `{total_videos}`\nTotal Parts: `{total_parts}`\n\n📦 **Part 1 shuru ho raha hai...**")
                trigger_background_loop(user_id)
                
            elif len(extracted_args) == 2:
                file_id, ch_num = extracted_args
                target_ch = CHANNELS.get(str(ch_num))
                if target_ch:
                    await bot.copy_message(chat_id=chat_id, from_chat_id=target_ch, message_id=int(file_id))
            else:
                await bot.send_message(chat_id=chat_id, text="👋 **Welcome!**\nVideos paane ke liye kisi video link par click karke aao!")
            return
    except Exception as err:
        print(f"❌ Error in text handler: {err}", flush=True)

# --- BUTTON CLICK HANDLER ---
async def handle_button_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    
    is_verified, _ = check_user_verification(user_id)
    if not is_verified:
        await query.message.reply_text("⏰ Aapka session khatam ho gaya! Kripya dobara verify karne ke liye /start likhein.")
        return

    try:
        if query.data == "get_next_part":
            state = states_col.find_one({"_id": user_id})
            if not state:
                await query.message.reply_text("❌ Session expired! Link par dobara click karein.")
                return
                
            current_part = state["current_part"] + 1
            if current_part > state["total_parts"]:
                await query.message.reply_text("🎉 Aapke saare Parts complete ho chuke hain!")
                states_col.delete_one({"_id": user_id})
                return
                
            states_col.update_one({"_id": user_id}, {"$set": {"current_part": current_part, "sent_in_current_part": 0}})
            await query.message.reply_text(f"📦 **Part {current_part} shuru ho raha hai...**")
            trigger_background_loop(user_id)
    except Exception as err:
        print(f"❌ Error in button handler: {err}", flush=True)

# --- MONGO-BASED BACKGROUND BATCH SENDING ---
async def send_video_chunk_logic(user_id):
    """Ek baar me sirf 5 videos bhejega taaki Vercel timeout na kare"""
    state = states_col.find_one({"_id": user_id})
    if not state:
        return

    chat_id = state["chat_id"]
    video_list = state["video_list"]
    target_ch = state["target_ch"]
    current_part = state["current_part"]
    videos_per_part = state["videos_per_part"]
    total_parts = state["total_parts"]
    sent_in_current_part = state["sent_in_current_part"]

    start_idx = ((current_part - 1) * videos_per_part) + sent_in_current_part
    end_part_idx = (current_part * videos_per_part)
    
    # Is chunk me kitni videos bhejni hain (Max 5)
    chunk_videos = video_list[start_idx:min(end_part_idx, len(video_list))][:5]

    if not chunk_videos:
        # Part complete ho gaya
        next_part_num = current_part + 1
        async with ptb_app:
            if next_part_num <= total_parts and end_part_idx < len(video_list):
                keyboard = [[InlineKeyboardButton(f"➡️ Get Part {next_part_num}", callback_data="get_next_part")]]
                await ptb_app.bot.send_message(
                    chat_id=chat_id,
                    text=f"⏸️ **Part {current_part} complete ho gaya hai!**\n\nAage ki videos (Part {next_part_num}) paane ke liye niche button par click karein 👇",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await ptb_app.bot.send_message(chat_id=chat_id, text="🎉 **SARA VIDEO COMPLETE HO GAYA!** ✅")
                states_col.delete_one({"_id": user_id})
        return

    # Send 5 videos
    async with ptb_app:
        for msg_id in chunk_videos:
            try:
                await ptb_app.bot.copy_message(chat_id=chat_id, from_chat_id=target_ch, message_id=msg_id)
                sent_in_current_part += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"❌ Msg {msg_id} failed: {e}")
                sent_in_current_part += 1

    # Save progress to MongoDB
    states_col.update_one({"_id": user_id}, {"$set": {"sent_in_current_part": sent_in_current_part}})
    
    # Self-Trigger for next 5 videos
    trigger_background_loop(user_id)

# --- INITIALIZE HANDLERS ---
ptb_app.add_handler(CommandHandler("start", handle_text_messages))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
ptb_app.add_handler(CallbackQueryHandler(handle_button_clicks))

# --- FLASK ENDPOINTS ---
@app.route('/', methods=['GET'])
def index():
    return "Bot is active via Permanent Serverless Architecture!", 200

@app.route('/favicon.ico', methods=['GET'])
def favicon():
    return "", 204

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update_json = request.get_json(force=True)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(ptb_app.initialize())
        update = Update.de_json(update_json, ptb_app.bot)
        loop.run_until_complete(ptb_app.process_update(update))
        loop.close()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/background-task', methods=['POST'])
def background_task():
    """Background loop context handler"""
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    if user_id:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_video_chunk_logic(user_id))
        loop.close()
    return jsonify({"status": "queued"}), 200

@app.errorhandler(404)
def page_not_found(e):
    return jsonify({"status": "ignored"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
