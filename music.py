import time
import asyncio
import threading
import yt_dlp
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

class DJPlugin:
    def __init__(self, bot):
        self.bot = bot
        self.sessions = {}  # { roomId: { 'pc', 'player', 'url' } }
        self.lock = threading.Lock()

    async def _get_stream_url(self, query):
        if not query.startswith("http"):
            query = f"ytsearch1:{query}"
        options = {"format": "bestaudio/best", "noplaylist": True, "quiet": True, "no_warnings": True}
        try:
            loop = asyncio.get_event_loop()
            with yt_dlp.YoutubeDL(options) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                if 'entries' in info:
                    info = info['entries'][0]
                return info.get('url'), info.get('title', 'Audio Track')
        except Exception as e:
            print(f"YT-DLP Error: {e}")
            return None, None

    def _run_async(self, coro):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    def handle_message(self, data):
        handler = data.get("handler")
        # Handle chat commands
        if handler == "chatroommessage":
            text = data.get("text", "").strip()
            if text.startswith("!"):
                parts = text[1:].split()
                cmd = parts[0].lower()
                args = parts[1:]
                room_id = data.get("roomid")

                if cmd == "play":
                    self._handle_play(room_id, args)
                elif cmd == "stop":
                    self._handle_stop(room_id)

        # Handle system signals for audio
        elif handler == "audioroom":
            self._handle_audio_signal(data)

    def _handle_play(self, room_id, args):
        if not args:
            self.bot.send_message(room_id, "Usage: `!play <song name>`")
            return

        query = " ".join(args)
        self.bot.send_message(room_id, f"🔍 Searching: **{query}**...")

        def start_playback():
            real_url, title = self._run_async(self._get_stream_url(query))
            if not real_url:
                self.bot.send_message(room_id, f"❌ '{query}' nahi mila.")
                return

            self.bot.send_message(room_id, f"🎶 **Playing:** {title}")
            with self.lock:
                # Agar pehle se gana chal raha hai to use band karo
                if room_id in self.sessions:
                    self._stop_internal(room_id)
                self.sessions[room_id] = {'url': real_url}

            # Join request bhejo (bhale hi pehle se joined ho, handshake ke liye zaroori hai)
            self.bot.send_json({"handler": "audioroom", "action": "join", "roomId": str(room_id)})

        threading.Thread(target=start_playback, daemon=True).start()

    def _handle_stop(self, room_id):
        self._stop_internal(room_id)
        # Send leave signal
        self.bot.send_json({"handler": "audioroom", "action": "leave", "roomId": str(room_id)})
        self.bot.send_message(room_id, "⏹️ Music Stopped and Left Stage.")
        # Auto re-join stage to be ready for next song
        time.sleep(1)
        self.bot.send_json({"handler": "audioroom", "action": "join", "roomId": str(room_id)})

    def _stop_internal(self, room_id):
        with self.lock:
            s = self.sessions.pop(room_id, None)
            if s:
                if 'player' in s and s['player']:
                    s['player'].stop()
                if 'pc' in s and s['pc']:
                    threading.Thread(target=self._run_async, args=(s['pc'].close(),), daemon=True).start()

    def _handle_audio_signal(self, data):
        msg_type = data.get("type")
        if msg_type == "transport-created":
            transports = data.get("transports", {})
            send_t = transports.get("send", {})
            
            room_id = None
            with self.lock:
                if self.sessions:
                    for rid, s in self.sessions.items():
                        if 'pc' not in s:
                            room_id = rid
                            break
            
            if not room_id: # If only joined, not playing
                # This part is tricky. For simplicity, we'll rely on play command to set room_id
                # but a more robust bot would get room_id from the join event in bot.py
                return

            if room_id and send_t:
                pc = RTCPeerConnection()
                with self.lock:
                    self.sessions[room_id]['pc'] = pc
                stream_url = self.sessions[room_id].get('url')

                if stream_url:
                    try:
                        player = MediaPlayer(stream_url)
                        if player and player.audio:
                            pc.addTrack(player.audio)
                            with self.lock:
                                self.sessions[room_id]['player'] = player
                    except Exception as e:
                        print(f"MediaPlayer Error: {e}")

                async def connect():
                    offer = await pc.createOffer()
                    await pc.setLocalDescription(offer)
                    fp = pc.localDescription.sdp.split("fingerprint:sha-256 ")[1].split("\r\n")[0]
                    
                    self.bot.send_json({"handler": "audioroom", "action": "connect-transport", "roomId": str(room_id), "direction": "send", "transportId": send_t.get("id"), "dtlsParameters": {"role": "client", "fingerprints": [{"algorithm": "sha-256", "value": fp}]}})
                    self.bot.send_json({"handler": "audioroom", "action": "transports-ready", "roomId": str(room_id)})
                    
                    # Sirf tabhi 'produce' karo jab gana bajana ho
                    if stream_url:
                        self.bot.send_json({"handler": "audioroom", "action": "produce", "roomId": str(room_id), "kind": "audio", "rtpParameters": {"codecs": [{"mimeType": "audio/opus", "payloadType": 111, "clockRate": 48000, "channels": 2, "parameters": {"minptime": 10, "useinbandfec": 1}}], "encodings": [{"ssrc": 11111111}]}, "requestId": int(time.time() * 1000)})
                    
                    print(f"[Audio] Handshake for {room_id} Complete.")

                threading.Thread(target=self._run_async, args=(connect(),), daemon=True).start()

        elif msg_type == "producer-created":
            print("[Audio] ✅ Stream is LIVE!")
