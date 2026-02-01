import websocket, json, threading, time, uuid, requests, traceback
import music

class HowdiesBot:
    def __init__(self):
        self.USERNAME = "kamina"
        self.PASSWORD = "p99665"
        self.DEFAULT_ROOM = "goodness"
        
        self.token = None
        self.user_id = None
        self.ws = None
        self.running = False
        self.current_room_id = None
        self.plugin = music.DJPlugin(self)

    def start(self):
        print(f"[DJ] Login attempt for: {self.USERNAME}")
        if self._login_api():
            self._connect_ws()

    def _login_api(self):
        try:
            r = requests.post("https://api.howdies.app/api/login", json={"username": self.USERNAME, "password": self.PASSWORD}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                self.token = data.get('token') or data.get('data', {}).get('token')
                self.user_id = data.get('id') or data.get('user', {}).get('id')
                print(f"[DJ] Login Success! ID: {self.user_id}")
                return True
            print(f"[DJ] API Error: {r.text}")
            return False
        except Exception as e:
            print(f"[DJ] Login Exception: {e}")
            return False

    def _connect_ws(self):
        if not self.token: return
        print("[DJ] WebSocket Connecting...")
        url = f"wss://app.howdies.app/howdies?token={self.token}"
        self.ws = websocket.WebSocketApp(url, on_open=self.on_open, on_message=self.on_message, on_error=self.on_error, on_close=self.on_close)
        self.running = True
        threading.Thread(target=lambda: self.ws.run_forever(ping_interval=15, ping_timeout=10), daemon=True).start()

    def on_open(self, ws):
        print("[DJ] WebSocket Connected. Sending login sequence...")
        self.send_json({"handler": "login", "username": self.USERNAME, "password": self.PASSWORD})
        threading.Timer(1, self._join_chat_room).start()

    def _join_chat_room(self):
        print(f"[DJ] Attempting to join chat room: {self.DEFAULT_ROOM}")
        self.send_json({"handler": "joinchatroom", "id": uuid.uuid4().hex, "name": self.DEFAULT_ROOM})

    def on_message(self, ws, msg): # <-- YAHAN BADLAV KIYA GAYA HAI
        try:
            data = json.loads(msg)
            print(f"[DJ BOT - Raw Message] Handler: {data.get('handler')}, Type: {data.get('type')}, Room: {data.get('roomid')}") # <-- DEBUG PRINT

            # --- ROOM_ID aur MY_ID update ---
            if data.get("handler") == "login":
                self.user_id = data.get("userid")
            elif data.get("handler") == "joinchatroom" and data.get("name") == self.DEFAULT_ROOM:
                self.current_room_id = data.get("roomid")
                print(f"[DJ] Joined Chat Room: {self.DEFAULT_ROOM} (ID: {self.current_room_id}). Auto-joining audio stage in 1s...")
                threading.Timer(1, self._auto_join_audio).start()
            
            # --- ALL MESSAGES TO PLUGIN ---
            # Har message ko plugin tak bhejo, plugin khud decide karega kya karna hai.
            self.plugin.handle_message(data)

        except Exception as e:
            traceback.print_exc()
    
    def _auto_join_audio(self):
        if self.current_room_id:
            print(f"[DJ] Sending audioroom join for ID: {self.current_room_id}.")
            self.send_json({"handler": "audioroom", "action": "join", "roomId": str(self.current_room_id)})
        else:
            print("[DJ Error] Cannot auto-join audio: current_room_id is None. Room join failed?")

    def on_error(self, ws, error): print(f"WS Error: {error}")
    def on_close(self, ws, _, __): 
        if self.running: 
            print("Connection lost. Reconnecting in 5s...")
            time.sleep(5)
            self._connect_ws()

    def send_json(self, data):
        if self.ws and self.ws.sock and self.ws.sock.connected: 
            self.ws.send(json.dumps(data))

    def send_message(self, room_id, text):
        self.send_json({"handler": "chatroommessage", "id": uuid.uuid4().hex, "type": "text", "roomid": str(room_id), "text": text})
