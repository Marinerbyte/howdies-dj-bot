import time
import asyncio
import threading
import yt_dlp
import re # Add this for robust regex
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
                    self._stop_internal(room_id) # Purana gana band karo
                self.sessions[room_id] = {'url': real_url}
            
            # Join request bhejo (bhale hi pehle se joined ho, handshake ke liye zaroori hai)
            self.bot.send_json({"handler": "audioroom", "action": "join", "roomId": str(room_id)})

        threading.Thread(target=start_playback, daemon=True).start()

    def _handle_stop(self, room_id):
        self._stop_internal(room_id)
        self.bot.send_json({"handler": "audioroom", "action": "leave", "roomId": str(room_id)})
        self.bot.send_message(room_id, "⏹️ Music Stopped and Left Stage.")
        # Auto re-join stage ko hata diya hai, kyuki ye connection issue kar raha tha
        # time.sleep(1)
        # self.bot.send_json({"handler": "audioroom", "action": "join", "roomId": str(room_id)})


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
            
            room_id = self.bot.current_room_id 
            
            if not room_id:
                print("[Audio Error] Could not determine room_id for transport-created.")
                return

            if room_id and send_t:
                print(f"[Audio Debug {room_id}] Initializing PeerConnection...")
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
                        print(f"[Audio Debug {room_id}] MediaPlayer created and track added.")
                    except Exception as e:
                        print(f"[Audio Error {room_id}] MediaPlayer Init failed: {e}")
                        return # Important: Agar MediaPlayer fail hua to aage mat badho

                async def connect():
                    print(f"[Audio Debug {room_id}] Creating WebRTC offer...")
                    offer = await pc.createOffer()
                    await pc.setLocalDescription(offer)
                    print(f"[Audio Debug {room_id}] Local description set. SDP: {pc.localDescription.sdp[:100]}...")
                    
                    sdp = pc.localDescription.sdp
                    # Robust fingerprint extraction
                    fp_match = re.search(r"fingerprint:sha-256 (.*)", sdp)
                    fp = fp_match.group(1).strip() if fp_match else "UNKNOWN_FP"
                    print(f"[Audio Debug {room_id}] Extracted DTLS Fingerprint: {fp}")
                    
                    self.bot.send_json({"handler": "audioroom", "action": "connect-transport", "roomId": str(room_id), "direction": "send", "transportId": send_t.get("id"), "dtlsParameters": {"role": "client", "fingerprints": [{"algorithm": "sha-256", "value": fp}]}})
                    print(f"[Audio Debug {room_id}] Sent connect-transport for {send_t.get('id')}.")
                    
                    await asyncio.sleep(0.5) # Timing adjust
                    send_req(self.bot, "transports-ready", room_id)
                    print(f"[Audio Debug {room_id}] Sent transports-ready.")

                    await asyncio.sleep(0.5) # Timing adjust
                    if stream_url:
                        self.bot.send_json({"handler": "audioroom", "action": "produce", "roomId": str(room_id), "kind": "audio", "rtpParameters": {"codecs": [{"mimeType": "audio/opus", "payloadType": 111, "clockRate": 48000, "channels": 2, "parameters": {"minptime": 10, "useinbandfec": 1}}], "encodings": [{"ssrc": 11111111}]}, "requestId": int(time.time() * 1000)})
                        print(f"[Audio Debug {room_id}] Sent produce request.")
                    
                    print(f"[Audio] Handshake for {room_id} Complete.")

                threading.Thread(target=self._run_async, args=(connect(),), daemon=True).start()

        elif msg_type == "producer-created":
            print("[Audio] ✅ Stream is LIVE!")
