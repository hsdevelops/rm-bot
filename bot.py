import logging
from os import getenv

from telegram.forcereply import ForceReply
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import ReplyKeyboardMarkup
import config
from sheets import SheetsService, edit_entry_single_field, edit_entry_multiple_fields, get_value, parse_time
from croniter import croniter

from datetime import datetime, timezone, timedelta
from helper import calc_next_run


# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

# Define a few command handlers. These usually take the two arguments update and
# context. Error handlers also receive the raised TelegramError object in error.
def start(update, context):
    """Send a message when the command /start is issued."""
    sheets_service = SheetsService()

    # timezone must be defined in order to create new job
    if sheets_service.retrieve_tz(update.message.chat.id) is None:
        update.message.reply_text(reply_markup=ForceReply(selective=True), text=config.start_message, parse_mode='MarkdownV2')
        return

    update.message.reply_text(config.simple_prompt_message, parse_mode='MarkdownV2')

def help(update, context):
    """Send a message when the command /help is issued."""
    update.message.reply_text(config.help_message, parse_mode='MarkdownV2')


def add(update, context):
    """Send a message when the command /add is issued."""
    sheets_service = SheetsService()

    # timezone must be defined in order to create new job
    if sheets_service.retrieve_tz(update.message.chat.id) is None:
        update.message.reply_text(reply_markup=ForceReply(selective=True), text=config.start_message, parse_mode='MarkdownV2')
        return

    update.message.reply_text(reply_markup=ForceReply(selective=True), text=config.request_jobname_message, parse_mode='MarkdownV2')

def delete(update, context):
    """Send a message when the command /delete is issued."""
    sheets_service = SheetsService()
    entries = sheets_service.get_entries_by_chatid(update.message.chat.id)
    keyboard = []
    for i, row in entries:
        if i % 2 == 0:
            keyboard.append([row["jobname"]])
            continue
        keyboard[len(keyboard)-1].append(row["jobname"])

    reply_markup = ReplyKeyboardMarkup(keyboard,
                                       one_time_keyboard=True,
                                       resize_keyboard=True)

    update.message.reply_text(config.delete_message, reply_markup=reply_markup)

def listjobs(update, context):
    """Send a message when the command /list is issued."""
    sheets_service = SheetsService()
    entries = sheets_service.get_entries_by_chatid(update.message.chat.id)
    reply_string = config.list_jobs_message
    for i, row in entries:
        reply_string = "{}\n\- {} \({}\)".format(reply_string, row["jobname"], row["crontab"].replace("*", "\\*"))
    if reply_string == config.list_jobs_message:
        reply_string = config.simple_prompt_message

    update.message.reply_text(reply_string, parse_mode='MarkdownV2')

def add_new_job(update):
    sheets_service = SheetsService()

    # timezone must be defined in order to create new job
    if sheets_service.retrieve_tz(update.message.chat.id) is None:
        update.message.reply_text(reply_markup=ForceReply(selective=True), text=config.start_message, parse_mode='MarkdownV2')
        return

    # check name does not already exist
    if sheets_service.check_exists(update.message.chat.id, update.message.text):
        update.message.reply_text(config.invalid_new_job_message, parse_mode='MarkdownV2')
        return

    # add job to db
    sheets_service.add_new_entry(update.message.chat.id, update.message.text)
    update.message.reply_text(reply_markup=ForceReply(selective=True), text=config.request_crontab_message, parse_mode='MarkdownV2')
    return

def add_timezone(update):
    # check validity
    try:
        tz_offset = int(update.message.text)
    except ValueError:
        update.message.reply_text(config.error_message, parse_mode='MarkdownV2')
        return

    if tz_offset < -12 or tz_offset > 14:
        update.message.reply_text(config.error_message, parse_mode='MarkdownV2')
        return

    sheets_service = SheetsService()
    sheets_service.add_tz(update.message.chat.id, tz_offset)
    update.message.reply_text(config.simple_prompt_message, parse_mode='MarkdownV2')

def add_crontab(update):
    if not croniter.is_valid(update.message.text): # crontab is not valid
        update.message.reply_text(
            reply_markup=ForceReply(selective=True), 
            text=config.invalid_crontab_message, 
            parse_mode='MarkdownV2')
        return

    sheets_service = SheetsService()
    entry_df = sheets_service.retrieve_latest_entry(update.message.chat.id)

    if len(entry_df) <= 0:
        update.message.reply_text(config.simple_prompt_message, parse_mode='MarkdownV2')
        return
    
    if len(get_value(entry_df, "crontab")) > 0: # field must be empty
        update.message.reply_text(config.prompt_new_job_message, parse_mode='MarkdownV2')
        return
    
    # update sheets entry
    updated_entry_df = edit_entry_single_field(entry_df, "crontab", update.message.text)
    sheets_service.update_entry(updated_entry_df)
    
    # reply
    update.message.reply_text(reply_markup=ForceReply(selective=True), text=config.request_text_message, parse_mode='MarkdownV2')


def add_message(update):
    sheets_service = SheetsService()
    entry_df = sheets_service.retrieve_latest_entry(update.message.chat.id)

    if len(entry_df) <= 0:
        update.message.reply_text(config.simple_prompt_message, parse_mode='MarkdownV2')
        return

    if len(get_value(entry_df, "content")) > 0: # field must be empty
        update.message.reply_text(config.prompt_new_job_message, parse_mode='MarkdownV2')
        return
    
    # arrange next run date and time
    crontab = get_value(entry_df, "crontab")
    user_tz_offset = sheets_service.retrieve_tz(update.message.chat.id)
    user_nextrun_ts, db_nextrun_ts = calc_next_run(crontab, user_tz_offset)

    # update sheets entry
    updated_entry_df = edit_entry_multiple_fields(entry_df, {
        "content": update.message.text,
        "nextrun_ts": db_nextrun_ts,
        "user_nextrun_ts": user_nextrun_ts
    })
    sheets_service.update_entry(updated_entry_df)

    # reply
    update.message.reply_text(config.confirm_message, parse_mode='MarkdownV2')


def remove_job(update):
    now = datetime.now(timezone(timedelta(hours=config.TZ_OFFSET)))

    sheets_service = SheetsService()
    entry_df = sheets_service.retrieve_specific_entry(update.message.chat.id, update.message.text)
    updated_entry_df = edit_entry_single_field(entry_df, "removed_ts", parse_time(now))
    sheets_service.update_entry(updated_entry_df)

    update.message.reply_text(text=config.delete_success_message, parse_mode='MarkdownV2')


def handle_messages(update, context):
    """Echo the user message."""
    reply_to_message = update.message.reply_to_message
    if reply_to_message is None:
        return
    text = reply_to_message.text
    if text == config.request_jobname_message:
        add_new_job(update)
    if text == config.request_crontab_message.replace("\\", "") \
        or text == config.invalid_crontab_message.replace("\\", ""):
        add_crontab(update)
    if text == config.request_text_message:
        add_message(update)
    if text == config.delete_message:
        remove_job(update)
    if text == config.start_message.replace("*", "").replace("\\", ""):
        add_timezone(update)

def error(update, context):
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def start_bot():
    """Start the bot."""
    # Create the Updater and pass it your bot's token.
    # Make sure to set use_context=True to use the new context based callbacks
    # Post version 12 this will no longer be necessary
    updater = Updater(config.TELEGARM_BOT_TOKEN, use_context=True)
    
    # stop updater if exists
    updater.stop()
    updater.is_idle = False

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help))
    dp.add_handler(CommandHandler("add", add))
    dp.add_handler(CommandHandler("delete", delete))
    dp.add_handler(CommandHandler("list", listjobs))

    # on noncommand i.e message
    dp.add_handler(MessageHandler(Filters.text, handle_messages))

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    if config.ENV:
        updater.start_webhook(
            listen="0.0.0.0", 
            port=int(getenv("PORT", 5000)),
            url_path=config.TELEGARM_BOT_TOKEN,
            webhook_url='%s/%s' % (config.BOTHOST, config.TELEGARM_BOT_TOKEN))
    else:
        updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()

if __name__ == '__main__':
    start_bot()