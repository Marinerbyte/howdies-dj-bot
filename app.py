import bot
import time
import sys

if __name__ == "__main__":
    print("Initializing DJ Bot...")
    dj_bot = bot.HowdiesBot()
    dj_bot.start()

    # Bot ko zinda rakhne ke liye
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down bot...")
        # Clean shutdown for music
        if dj_bot.plugin and dj_bot.current_room_id:
            dj_bot.plugin._stop_internal(dj_bot.current_room_id)
        sys.exit(0)
