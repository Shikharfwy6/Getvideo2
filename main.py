import logging
import asyncio
import sys
import traceback
import math
import secrets
import string
import requests
import os
import telegram
from datetime import datetime, timedelta, time
import pytz  # Raat ke 12 baje IST reset ke liye timezone support
from pymongo import MongoClient
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, CommandHandler, filters, ContextTypes

# --- LOGGING SETUP ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO, stream=sys.stdout)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
BOT_USERNAME = "Getvideo81827_bot"

if not BOT_TOKEN or not MONGO_URI:
    print("💥 Critical Error: BOT_TOKEN ya MONGO_URI missing hai!", flush=True)
    sys.exit(1)

CHANNELS = {
    "1": "-1003952628014",
    "2": "-1003758252316",
    "3": "-1003736158308",
    "4": "-1003195006898",
    "5": "-1003307449853"
}

# Updated Shorteners APIs (Arolinks, Vplink, Instantlinks)
SHORTENERS = {
    "arolinks": "https://arolinks.com/api?api=f4617908b561110a219cd2b65bc255c2c2c6ff8a&url={url}",
    "vplink": "https://vplink.in/api?api=017ab25e4402465d00047e8e2897f3c6b38afbd9&url={url}",
    "instantlinks": "https://instantlinks.co/api?api=323c4585c0d0b8bc04a170cd57a2e6a74ac6d8aa&url={url}"
}

# --- MONGODB SETUP ---
try:
    mongo_client = MongoClient(MONGO_URI, maxPoolSize=5, minPoolSize=1, waitQueueTimeoutMS=2000, retryWrites=True)
    db = mongo_client["cluster_bot_db"]
    users_col = db["verified_users"]
    print("✅ MongoDB Connected with New Schema Rules!", flush=True)
except Exception as e:
    print(f"💥 MongoDB Connection Error: {e}", flush=True)
    sys.exit(1)

USER_STATES = {}
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

app = Flask(name)
ptb_app = Application.builder().token(BOT_TOKEN).build()
ptb_app.bot._username = BOT_USERNAME
ptb_app.bot._bot_user = telegram.User(id=int(BOT_TOKEN.split(':')[0]), is_bot=True, first_name="Getvideo", username=BOT_USERNAME)

IST = pytz.timezone('Asia/Kolkata')

# --- HELPER FUNCTIONS ---

def get_short_link(api_name, long_url):
    try:
        api_url = SHORTENERS[api_name].format(url=long_url)
        response = session.get(api_url, timeout=5)
        if response.status_code == 200:
            res_text = response.text.strip()
            if "https://" in res_text or "http://" in res_text:
                return res_text
            return response.json().get("shortenedUrl", None)
    except Exception as e:
        print(f"❌ Shortener Error ({api_name}): {e}", flush=True)
    return None

def get_ist_midnight():
    """Aaj ki raat ke 12:00 baje (Midnight) ka IST time object nikalne ke liye"""
    now_ist = datetime.now(IST)
    midnight = now_ist.replace(hour=23, minute=59, second=59, microsecond=0)
    return midnight.astimezone(pytz.utc)

def check_user_verification(user_id):
    """
    User status verify karta hai aur uske credit check karta hai.
    Raat ke 12 baje ke baad automatically unverified ho jayega.
    """
    try:
        user = users_col.find_one({"_id": user_id})
        now = datetime.utcnow()
        
        if user:
            # Agar expiry time nikal gaya hai to unverified karo
            if user.get("expire_at") and user.get("expire_at").replace(tzinfo=pytz.utc) < now.replace(tzinfo=pytz.utc):
                users_col.update_one({"_id": user_id}, {"$set": {"status": "unverified", "available_request": "0"}})
                return False, user

            if user.get("status") == "verified":
                reqs = user.get("available_request", "0")
                if reqs == "unlimited":
                    return True, user
                try:
                    if int(reqs) > 0:
                        return True, user
                except ValueError:
                    pass
                
                # Agar requests khatam ho chuki hain
                users_col.update_one({"_id": user_id}, {"$set": {"status": "unverified"}})
                return False, user
            return False, user
    except Exception as e:
        print(f"⚠️ MongoDB Read Fail: {e}", flush=True)
    return False, None

def generate_random_token(length=12):
    return "v_" + ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(length))

def make_nested_link(steps, target_url):
    """Links ko multiple levels me chain short karne ke liye function"""
    current_url = target_url
    for step in steps:
        short = get_short_link(step, current_url)
        if short:
            current_url = short
        else:
            print(f"⚠️ Chain shortener failed for {step}, bypassing step.", flush=True)
    return current_url

# --- COMMAND & TEXT/MEDIA HANDLERS ---

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
        
    bot = context.bot
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    text_message = update.message.text.strip() if update.message.text else ""
    
    try:
        if text_message.startswith("/start"):
            parts = text_message.split()
            raw_arg = parts[1] if len(parts) > 1 else ""
            
            # --- 1. VERIFICATION CHECKBACK ROUTE ---
            if raw_arg.startswith("v_"):
                # Token format: v_v1_TOKEN, v_v2_TOKEN, v_v3_TOKEN
                token_parts = raw_arg.split('_')
                if len(token_parts) < 3:
                    await bot.send_message(chat_id=chat_id, text="❌ Invalid verification link format!")
                    return
                
                v_type = token_parts[1] # v1, v2, v3
                user_record = users_col.find_one({"_id": user_id, "token": raw_arg})
                
                if user_record:
                    now = datetime.utcnow()
                    if v_type == "v1":
                        expire_time = now + timedelta(hours=2)
                        req_count = "3"
                        api_used = "arolink"
                    elif v_type == "v2":
                        expire_time = now + timedelta(hours=3)
                        req_count = "5"
                        # Agar pehle se v1 ya v2 ho chuka tha, to tracking ke hisab se dynamically use save karo
                        api_used = "arolink aur vplink" if "arolink" not in user_record.get("current_api", "") else "vplink"
                    else:  # v3
                        expire_time = get_ist_midnight() # Raat ke 12 baje expiry
                        req_count = "unlimited"
                        api_used = "complete" if "vplink" not in user_record.get("current_api", "") else "instalink aur vplink"

                    # Database Update as per your exact format requirement
                    users_col.update_one(
                        {"_id": user_id},
                        {"$set": {
                            "status": "verified",
                            "current_api": api_used,
                            "User": "normal premium",
                            "available_request": req_count,
                            "token": None,
                            "expire_at": expire_time
                        }},
                        upsert=True
                    )
                    await bot.send_message(chat_id=chat_id, text=f"✅ **Verification Successful!**\n\nAapko **{req_count} requests** mil gayi hain. 🎉")
                else:
                    await bot.send_message(chat_id=chat_id, text="❌ Invalid ya Expired verification token!")
                return

            # --- 2. MAIN REQUEST PROCESSOR ---
            is_verified, user_data = check_user_verification(user_id)
            
            if not is_verified:
                # Naye users ya Reset users ke liye default setup
                current_api_status = user_data.get("current_api", "") if user_data else ""
                
                keyboard = []
                unique_base = generate_random_token()
                
                # Check karo pehle kya use ho chuka hai (Ad counts optimization)
                has_done_v1 = "arolink" in current_api_status
                has_done_v2 = "vplink" in current_api_status or "complete" in current_api_status

                # OPTION 1: Sirf tab dikhao jab pehle v1 ya v2 na kiya ho
                if not has_done_v1 and not has_done_v2:
                    t_v1 = f"v_v1_{unique_base}"
                    users_col.update_one({"_id": user_id}, {"$set": {"token": t_v1, "status": "unverified", "User": "normal premium", "available_request": "3,5,unlimited"}}, upsert=True)
                    dest_v1 = f"https://t.me/{BOT_USERNAME}?start={t_v1}"
                    link_v1 = make_nested_link(["arolinks"], dest_v1)
                    keyboard.append([InlineKeyboardButton("🔐 Verify 1 (4 Page Ads | 3 Req | 2 Hours)", url=link_v1)])
                
                # OPTION 2: Agar user v2 direct kar raha hai ya v1 ke baad aaya hai
                if not has_done_v2:
                    t_v2 = f"v_v2_{unique_base}"
                    users_col.update_one({"_id": user_id}, {"$set": {"token": t_v2, "status": "unverified", "User": "normal premium", "available_request": "3,5,unlimited"}}, upsert=True)
                    dest_v2 = f"https://t.me/{BOT_USERNAME}?start={t_v2}"
                    
                    # Agar v1 pehle ho chuka hai to Arolink skip karo, sirf vplink use karo
                    steps_v2 = ["vplink"] if has_done_v1 else ["arolinks", "vplink"]
                    link_v2 = make_nested_link(steps_v2, dest_v2)
                    
                    text_v2 = "🔐 Verify 2 (4 Page Ads | 5 Req | 3 Hours)" if has_done_v1 else "🔐 Verify 2 (8 Page Ads | 5 Req | 3 Hours)"
                    keyboard.append([InlineKeyboardButton(text_v2, url=link_v2)])

                # OPTION 3: Hamesha available rahega jab tak fully complete na ho
                t_v3 = f"v_v3_{unique_base}"
                users_col.update_one({"_id": user_id}, {"$set": {"token": t_v3, "status": "unverified", "User": "normal premium", "available_request": "3,5,unlimited"}}, upsert=True)
                dest_v3 = f"https://t.me/{BOT_USERNAME}?start={t_v3}"
                
                # Agar user option 2 ke baad aa raha hai to Arolink skip (instalink + vplink), warna complete chain
                steps_v3 = ["vplink", "instantlinks"] if has_done_v2 else ["arolinks", "vplink", "instantlinks"]
                link_v3 = make_nested_link(steps_v3, dest_v3)
                
                text_v3 = "🔐 Verify 3 (4 Page Ads | Unlimited Requests | 24h)" if has_done_v2 else "🔐 Verify 3 (12 Page Ads | Unlimited Requests | 24h)"
                keyboard.append([InlineKeyboardButton(text_v3, url=link_v3)])

                if not keyboard:
                    # Fallback agar edge-case me sab completed ho aur credits zero ho jayein
                    users_col.update_one({"_id": user_id}, {"$set": {"current_api": ""}})
                    await bot.send_message(chat_id=chat_id, text="🔄 Aapka daily session state refresh ho gaya hai. Dobara try karne ke liye /start karein.")
                    return

                await bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ **Access Denied!**\n\nAapko file paane ke liye kisi ek Option se verify karna hoga:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            # --- 3. DEDUCT REQUEST & DELIVER CONTENT ---
            reqs = user_data.get("available_request", "0")
            if reqs != "unlimited":
                try:
                    new_reqs = str(max(0, int(reqs) - 1))
                    users_col.update_one({"_id": user_id}, {"$set": {"available_request": new_reqs}})
                except:
                    pass

            # Standard batching logic handler
            extracted_args = raw_arg.split('_') if "_" in raw_arg else [raw_arg]
            if len(extracted_args) == 4:
                start_id, end_id, ch_num, total_parts = map(int, extracted_args)
                video_list = list(range(start_id, end_id + 1))
                target_ch = CHANNELS.get(str(ch_num))
                if not target_ch: return
                
                total_videos = len(video_list)
                videos_per_part = math.ceil(total_videos / total_parts)
                USER_STATES[user_id] = {"video_list": video_list, "target_ch": target_ch, "videos_per_part": videos_per_part, "current_part": 1, "total_parts": total_parts, "total_videos": total_videos}
                await bot.send_message(chat_id=chat_id, text=f"📊 **Verification Valid!**\nTotal Files: `{total_videos}`\n\n📦 **Part 1 shuru ho raha hai...**")
                await send_video_batch(chat_id, bot, user_id)
            elif len(extracted_args) == 2:
                file_id, ch_num = extracted_args
                target_ch = CHANNELS.get(str(ch_num))
                if target_ch:
                    await bot.copy_message(chat_id=chat_id, from_chat_id=target_ch, message_id=int(file_id))
            else:
                await bot.send_message(chat_id=chat_id, text="👋 **Welcome Back!**\nAapka verification active hai. Kisi bhi link se files paane ke liye click karein!")
            return
    except Exception as err:
        print(f"❌ Error in message handler: {err}", flush=True)
        traceback.print_exc()

# --- BUTTON CLICK HANDLER FOR BATCH PARTS ---
async def handle_button_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    await query.answer()
    
    is_verified, _ = check_user_verification(user_id)
    if not is_verified:
        await query.message.reply_text("⏰ Session expired ya requests limit khatam! Dobara verify karein.")
        return

    if query.data == "get_next_part":
        if user_id not in USER_STATES: return
        state = USER_STATES[user_id]
        current_part = state["current_part"] + 1
        if current_part > state["total_parts"]:
            await query.message.reply_text("🎉 Saare Parts complete ho chuke hain!")
            del USER_STATES[user_id]
            return
        state["current_part"] = current_part
        await query.message.reply_text(f"📦 **Part {current_part} shuru ho raha hai...**")
        await send_video_batch(chat_id, context.bot, user_id)

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

    for msg_id in current_batch:
        try:
            await bot.copy_message(chat_id=chat_id, from_chat_id=target_ch, message_id=msg_id)
            await asyncio.sleep(0.5) 
        except:
            pass
            
    if current_part < total_parts:
        keyboard = [[InlineKeyboardButton(f"➡️ Get Part {current_part + 1}", callback_data="get_next_part")]]
        await bot.send_message(chat_id=chat_id, text=f"⏸️ **Part {current_part} complete!**", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await bot.send_message(chat_id=chat_id, text="🎉 **SAARI FILES COMPLETE HO GAYI!** ✅")
        if user_id in USER_STATES: del USER_STATES[user_id]

# --- HANDLERS REGISTRATION ---
ptb_app.add_handler(CommandHandler("start", handle_text_messages))
ptb_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_text_messages))
ptb_app.add_handler(CallbackQueryHandler(handle_button_clicks))

@app.route('/', methods=['GET'])
def index():
    return "Vercel/Render Serverless Engine Engine Live with Custom Multiple Chaining & Dynamic Reset!", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.method == "POST":
        try:
            update_json = request.get_json(force=True)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
            ptb_app._initialized = True
            ptb_app.bot._initialized = True
            
            update = Update.de_json(update_json, ptb_app.bot)
            loop.run_until_complete(ptb_app.process_update(update))
            return jsonify({"status": "success"}), 200
        except Exception as e:
            print(f"💥 Webhook Process Error: {e}", flush=True)
            return jsonify({"status": "error"}), 200
    return "Method Not Allowed", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7860)
