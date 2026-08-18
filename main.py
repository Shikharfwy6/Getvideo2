import logging
import asyncio
import sys
import traceback
import math
import os
import telegram
from datetime import datetime, timedelta
import pytz  
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
ADMIN_ID = 8838634478  # ✅ Admin ID
FORCE_SUB_CHANNEL = -1003624680009  # 🔒 Force Subscribe Channel ID

if not BOT_TOKEN or not MONGO_URI:
    print("💥 Critical Error: BOT_TOKEN ya MONGO_URI missing hai!", flush=True)
    sys.exit(1)

CHANNELS = {
    "1": "-1004435774367",
    "2": "-1003758252316",
    "3": "-1003307449853",
    "4": "-1003195006898",
    "5": "-1003307449853",
    "6": "-1003901369992",
    "7": "-1003400249450",
    "8": "-1003211122364"
}

# --- MONGODB SETUP ---
try:
    mongo_client = MongoClient(MONGO_URI, maxPoolSize=5, minPoolSize=1, waitQueueTimeoutMS=2000, retryWrites=True)
    db = mongo_client["cluster_bot_db"]
    users_col = db["verified_users"]
    settings_col = db["settings"] # ✅ Settings collection for links & secret verify token
    print("✅ MongoDB Connected Successfully!", flush=True)
except Exception as e:
    print(f"💥 MongoDB Connection Error: {e}", flush=True)
    sys.exit(1)

USER_STATES = {}

app = Flask(__name__)
ptb_app = Application.builder().token(BOT_TOKEN).build()
ptb_app.bot._username = BOT_USERNAME
ptb_app.bot._bot_user = telegram.User(id=int(BOT_TOKEN.split(':')[0]), is_bot=True, first_name="Getvideo", username=BOT_USERNAME)

IST = pytz.timezone('Asia/Kolkata')

# --- 🔒 FORCE SUBSCRIBE CHECK FUNCTION ---
async def is_user_subscribed(bot, user_id):
    try:
        member = await bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        if member.status in ['creator', 'administrator', 'member']:
            return True
        return False
    except Exception as e:
        print(f"⚠️ Force Sub Check Error: {e}", flush=True)
        return False

# --- ⏱️ CLEANUP EXPIRED MESSAGES ---
async def clean_expired_files(bot, user_id, chat_id):
    try:
        user = users_col.find_one({"_id": user_id})
        if not user or "active_files" not in user:
            return

        now = datetime.utcnow()
        expired_messages = []

        for file_info in user["active_files"]:
            if file_info["delete_at"].replace(tzinfo=pytz.utc) <= now.replace(tzinfo=pytz.utc):
                msg_id = file_info["message_id"]
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception:
                    pass
                expired_messages.append(file_info)

        if expired_messages:
            users_col.update_one(
                {"_id": user_id},
                {"$pull": {"active_files": {"message_id": {"$in": [x["message_id"] for x in expired_messages]}}}}
            )
    except Exception as e:
        print(f"❌ Error in clean_expired_files: {e}", flush=True)

# --- HELPER FUNCTIONS ---
def check_user_verification(user_id):
    try:
        user = users_col.find_one({"_id": user_id})
        now = datetime.utcnow()
        
        if user:
            if user.get("User") == "premium":
                return True, user

            if user.get("expire_at") and user.get("expire_at").replace(tzinfo=pytz.utc) < now.replace(tzinfo=pytz.utc):
                users_col.update_one({"_id": user_id}, {"$set": {"status": "unverified", "available_request": "0"}})
                return False, user

            if user.get("status") == "verified":
                return True, user
                
            return False, user
    except Exception as e:
        print(f"⚠️ MongoDB Read Fail: {e}", flush=True)
    return False, None

def get_setting(key):
    setting = settings_col.find_one({"_id": key})
    if setting and "value" in setting:
        return setting["value"]
    return None

# --- ADMIN COMMANDS ---

# 1. Verification Short Link Setting
async def handle_todaylink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!")
        return

    if not context.args:
        await update.message.reply_text("💡 **Sahi Format:** `/todaylink https://vplink.in/KNrExH`")
        return

    new_link = context.args[0].strip()
    settings_col.update_one(
        {"_id": "today_link"},
        {"$set": {"value": new_link, "updated_at": datetime.utcnow()}},
        upsert=True
    )
    await update.message.reply_text(f"✅ **Today's Short Link Updated!**\n\n🔗 Short Link: `{new_link}`")

# 2. Verification Secret Token Check Setting
async def handle_todaycheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!")
        return

    if not context.args:
        await update.message.reply_text("💡 **Sahi Format:** `/todaycheck https://t.me/Getvideo81827_bot?start=verifyiquahavVahqjqba`\nya fir direct token: `/todaycheck verifyiquahavVahqjqba`")
        return

    input_text = context.args[0].strip()
    # Agar poora Telegram URL bhej diya jaye to start= ke baad ka secret nikal lo
    if "start=" in input_text:
        secret_token = input_text.split("start=")[-1]
    else:
        secret_token = input_text

    settings_col.update_one(
        {"_id": "today_check_token"},
        {"$set": {"value": secret_token, "updated_at": datetime.utcnow()}},
        upsert=True
    )
    await update.message.reply_text(f"🔑 **Secret Verification Token Set!**\n\n🎯 Active Token: `{secret_token}`")

# 3. Premium Admin Command
async def handle_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!")
        return

    if not context.args:
        await update.message.reply_text("💡 **Sahi Format:** `/p target_user_id`")
        return

    try:
        target_uid = int(context.args[0])
        users_col.update_one(
            {"_id": target_uid},
            {"$set": {
                "User": "premium",
                "status": "verified",
                "available_request": "unlimited",
                "expire_at": None
            }},
            upsert=True
        )
        await update.message.reply_text(f"👑 **Success!** User `{target_uid}` ko lifelong **Premium** member bana diya gaya hai.")
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID!")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

# --- MAIN MESSAGE & COMMAND HANDLER ---
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
        
    bot = context.bot
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    text_message = update.message.text.strip() if update.message.text else ""
    
    # 🚨 1. FORCE SUBSCRIBE CHECK 🚨
    if not await is_user_subscribed(bot, user_id):
        try:
            chat_info = await bot.get_chat(FORCE_SUB_CHANNEL)
            invite_link = chat_info.invite_link or f"https://t.me/c/{str(FORCE_SUB_CHANNEL)[4:]}"
        except Exception:
            invite_link = "https://t.me"

        start_param = text_message.split()[1] if len(text_message.split()) > 1 else ""
        try_again_url = f"https://t.me/{BOT_USERNAME}?start={start_param}" if start_param else f"https://t.me/{BOT_USERNAME}"

        keyboard = [
            [InlineKeyboardButton("📢 Join Channel First", url=invite_link)],
            [InlineKeyboardButton("🔄 Try Again", url=try_again_url)]
        ]
        await bot.send_message(
            chat_id=chat_id,
            text="❌ **Access Denied!**\n\nBot ko use karne ke liye pehle hamare official channel ko join karein. Join karne ke baad **Try Again** par click karein.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    await clean_expired_files(bot, user_id, chat_id)
    
    try:
        if text_message.startswith("/start"):
            parts = text_message.split()
            raw_arg = parts[1] if len(parts) > 1 else ""
            
            # --- 2. SECRET TOKEN VERIFICATION ROUTE ---
            active_secret_token = get_setting("today_check_token")
            
            # Agar start parameter exact Secret token se match karta hai
            if active_secret_token and raw_arg == active_secret_token:
                now = datetime.utcnow()
                expire_time = now + timedelta(hours=24) # 24 Ghante ki validity

                users_col.update_one(
                    {"_id": user_id},
                    {"$set": {
                        "status": "verified",
                        "User": "normal",
                        "available_request": "unlimited",
                        "expire_at": expire_time
                    }},
                    upsert=True
                )
                await bot.send_message(chat_id=chat_id, text="✅ **Verification Successful!**\n\nAapko **24 Ghante** ke liye **Unlimited Access** mil gaya hai. 🎉")
                return

            # --- 3. MAIN REQUEST & ACCESS CHECK ---
            is_verified, user_data = check_user_verification(user_id)
            
            if not is_verified:
                today_link = get_setting("today_link")
                if not today_link:
                    await bot.send_message(chat_id=chat_id, text="⚠️ Admin ne abhi tak verification link set nahi kiya hai. Kripya baad me try karein.")
                    return

                keyboard = [[InlineKeyboardButton("🔐 Click Here to Verify (24h Access)", url=today_link)]]

                await bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ **Access Denied!**\n\nBot ko use karne ke liye niche diye gaye button par click karke verify karein. Verification 24 ghante ke liye valid rahega:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            # --- 4. DELIVER CONTENT ---
            extracted_args = raw_arg.split('_') if "_" in raw_arg else [raw_arg]
            
            # Batch Mode
            if len(extracted_args) == 4:
                start_id, end_id, ch_num, total_parts = map(int, extracted_args)
                video_list = list(range(start_id, end_id + 1))
                target_ch = CHANNELS.get(str(ch_num))
                if not target_ch: return
                
                total_videos = len(video_list)
                videos_per_part = math.ceil(total_videos / total_parts)
                USER_STATES[user_id] = {"video_list": video_list, "target_ch": target_ch, "videos_per_part": videos_per_part, "current_part": 1, "total_parts": total_parts, "total_videos": total_videos}
                await bot.send_message(chat_id=chat_id, text=f"📊 **Verification Valid!**\nTotal Files: `{total_videos}`\n\n⚠️ *Note: Saari files milne ke 5 mins baad auto-delete ho jayengi!*")
                await send_video_batch(chat_id, bot, user_id)
                
            # Single File Mode
            elif len(extracted_args) == 2:
                file_id, ch_num = extracted_args
                target_ch = CHANNELS.get(str(ch_num))
                if target_ch:
                    sent_msg = await bot.copy_message(chat_id=chat_id, from_chat_id=target_ch, message_id=int(file_id))
                    
                    now = datetime.utcnow()
                    delete_at = now + timedelta(minutes=5)
                    
                    users_col.update_one(
                        {"_id": user_id},
                        {"$push": {
                            "active_files": {
                                "message_id": sent_msg.message_id,
                                "give_time": now,
                                "delete_at": delete_at
                            }
                        }}
                    )
            else:
                await bot.send_message(chat_id=chat_id, text="👋 **Welcome Back!**\nAapka 24-hour verification active hai.")
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
    
    if not await is_user_subscribed(context.bot, user_id):
        await query.message.reply_text("❌ Aapne mandatory channel join nahi kiya hai! Pehle channel subscribe karein.")
        return

    await clean_expired_files(context.bot, user_id, chat_id)
    
    is_verified, _ = check_user_verification(user_id)
    if not is_verified:
        await query.message.reply_text("⏰ Aapka 24-hour session expire ho gaya hai! Kripya dobara verify karein.")
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

    now = datetime.utcnow()
    delete_at = now + timedelta(minutes=5)

    for msg_id in current_batch:
        try:
            sent_msg = await bot.copy_message(chat_id=chat_id, from_chat_id=target_ch, message_id=msg_id)
            users_col.update_one(
                {"_id": user_id},
                {"$push": {
                    "active_files": {
                        "message_id": sent_msg.message_id,
                        "give_time": now,
                        "delete_at": delete_at
                    }
                }}
            )
            await asyncio.sleep(0.6) 
        except:
            pass
            
    if current_part < total_parts:
        keyboard = [[InlineKeyboardButton(f"➡️ Get Part {current_part + 1}", callback_data="get_next_part")]]
        await bot.send_message(chat_id=chat_id, text=f"⏸️ **Part {current_part} complete!**\n*Bheji gayi files 5 mins baad automatic delete ho jayengi.*", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await bot.send_message(chat_id=chat_id, text="🎉 **SAARI FILES COMPLETE HO GAYI!** ✅")
        if user_id in USER_STATES: del USER_STATES[user_id]

# --- HANDLERS REGISTRATION ---
ptb_app.add_handler(CommandHandler("todaylink", handle_todaylink_command))
ptb_app.add_handler(CommandHandler("todaycheck", handle_todaycheck_command)) # ✅ New Command Handler
ptb_app.add_handler(CommandHandler("p", handle_premium_command))
ptb_app.add_handler(CommandHandler("start", handle_text_messages))
ptb_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_text_messages))
ptb_app.add_handler(CallbackQueryHandler(handle_button_clicks))

@app.route('/', methods=['GET'])
def index():
    return "Bot is running with double secret token verification system!", 200

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

app = app

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7860)
