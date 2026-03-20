"""Camera Bridge - WebSocket server that accepts phone camera frames."""
import hmac
import secrets
import time
from collections import deque
from typing import Any
import ssl
import qrcode
import io
from websockets.asyncio.server import serve, ServerConnection


class PairingManager:
    """Manages one-time pairing tokens for secure phone connection."""

    def __init__(self, expiry_seconds: int = 60):
        self.expiry_seconds = expiry_seconds
        self.pending_token: str | None = None
        self._created_at: float = 0

    def generate_token(self) -> str:
        """Generate a 128-bit random hex token."""
        self.pending_token = secrets.token_hex(32)
        self._created_at = time.time()
        return self.pending_token

    def validate_token(self, token: str) -> bool:
        """Validate and consume a pairing token (one-time use)."""
        if self.pending_token is None:
            return False
        if time.time() - self._created_at > self.expiry_seconds:
            self.pending_token = None
            return False
        # Use timing-safe comparison to prevent timing attacks
        if not hmac.compare_digest(token, self.pending_token):
            return False
        self.pending_token = None  # Consume
        return True

    def get_pairing_url(self, host: str, port: int) -> str:
        """Generate pairing URL with token."""
        return f"https://{host}:{port}?token={self.pending_token}"


class FrameQueue:
    """Thread-safe ring buffer for camera frames."""

    def __init__(self, max_size: int = 30):
        self._frames: deque = deque(maxlen=max_size)

    def push(self, frame_bytes: bytes, timestamp: float):
        """Add a frame to the queue (evicts oldest if full)."""
        self._frames.append((frame_bytes, timestamp))

    def get_latest(self) -> tuple[bytes, float] | None:
        """Get the most recent frame, or None if empty."""
        return self._frames[-1] if self._frames else None

    def get_all(self) -> list[tuple[bytes, float]]:
        """Get all frames in the queue."""
        return list(self._frames)

    def clear(self):
        """Remove all frames from the queue."""
        self._frames.clear()

    def __len__(self):
        return len(self._frames)


PHONE_APP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Screen Vision Camera</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #000;
            color: #fff;
            overflow: hidden;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        #video-container {
            flex: 1;
            position: relative;
            background: #000;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        #video {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        #canvas {
            display: none;
        }
        #controls {
            position: absolute;
            bottom: 20px;
            left: 0;
            right: 0;
            display: flex;
            justify-content: center;
            gap: 15px;
            padding: 0 20px;
        }
        button {
            background: rgba(255, 255, 255, 0.2);
            border: 2px solid rgba(255, 255, 255, 0.3);
            border-radius: 50px;
            color: #fff;
            font-size: 16px;
            padding: 15px 30px;
            cursor: pointer;
            backdrop-filter: blur(10px);
            transition: all 0.2s;
            font-weight: 600;
        }
        button:active {
            transform: scale(0.95);
            background: rgba(255, 255, 255, 0.3);
        }
        button.active {
            background: rgba(16, 185, 129, 0.3);
            border-color: rgba(16, 185, 129, 0.6);
        }
        button.recording {
            background: rgba(239, 68, 68, 0.3);
            border-color: rgba(239, 68, 68, 0.6);
        }
        #status {
            position: absolute;
            top: 20px;
            left: 20px;
            right: 20px;
            background: rgba(0, 0, 0, 0.7);
            padding: 15px;
            border-radius: 10px;
            backdrop-filter: blur(10px);
            font-size: 14px;
        }
        .status-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 5px;
        }
        .status-row:last-child {
            margin-bottom: 0;
        }
        .status-label {
            opacity: 0.7;
        }
        .status-value {
            font-weight: 600;
        }
        .connected {
            color: #10b981;
        }
        .disconnected {
            color: #ef4444;
        }
        .pairing {
            color: #f59e0b;
        }
    </style>
</head>
<body>
    <div id="video-container">
        <video id="video" autoplay playsinline></video>
        <canvas id="canvas"></canvas>
        <div id="status">
            <div class="status-row">
                <span class="status-label">Status:</span>
                <span class="status-value" id="connection-status">Connecting...</span>
            </div>
            <div class="status-row">
                <span class="status-label">Frames:</span>
                <span class="status-value" id="frame-count">0</span>
            </div>
            <div class="status-row">
                <span class="status-label">Audio:</span>
                <span class="status-value" id="audio-status">Disabled</span>
            </div>
        </div>
        <div id="controls">
            <button id="start-btn">Start</button>
            <button id="flip-btn">Flip</button>
            <button id="mic-btn">Mic</button>
        </div>
    </div>

    <script>
        const video = document.getElementById('video');
        const canvas = document.getElementById('canvas');
        const ctx = canvas.getContext('2d');
        const startBtn = document.getElementById('start-btn');
        const flipBtn = document.getElementById('flip-btn');
        const micBtn = document.getElementById('mic-btn');
        const connectionStatus = document.getElementById('connection-status');
        const frameCount = document.getElementById('frame-count');
        const audioStatus = document.getElementById('audio-status');

        let ws = null;
        let stream = null;
        let audioStream = null;
        let isStreaming = false;
        let facingMode = 'environment'; // rear camera
        let micEnabled = false;
        let frameCounter = 0;
        let captureInterval = null;
        let mediaRecorder = null;

        // Get token from URL
        const urlParams = new URLSearchParams(window.location.search);
        const token = urlParams.get('token');

        if (!token) {
            alert('No pairing token provided. Please scan the QR code again.');
        }

        // Connect to WebSocket
        function connectWebSocket() {
            const wsUrl = `wss://${window.location.host}/ws`;
            ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                console.log('WebSocket connected');
                // Send token as first message
                ws.send(token);
                connectionStatus.textContent = 'Paired';
                connectionStatus.className = 'status-value connected';
            };

            ws.onclose = () => {
                console.log('WebSocket disconnected');
                connectionStatus.textContent = 'Disconnected';
                connectionStatus.className = 'status-value disconnected';
                stopStreaming();
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                connectionStatus.textContent = 'Error';
                connectionStatus.className = 'status-value disconnected';
            };
        }

        // Initialize camera
        async function initCamera() {
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: facingMode },
                    audio: false
                });
                video.srcObject = stream;
                console.log('Camera initialized');
            } catch (error) {
                console.error('Error accessing camera:', error);
                alert('Could not access camera. Please grant camera permissions.');
            }
        }

        // Start streaming frames
        function startStreaming() {
            if (!ws || ws.readyState !== WebSocket.OPEN) {
                alert('Not connected to server');
                return;
            }

            isStreaming = true;
            startBtn.textContent = 'Pause';
            startBtn.classList.add('recording');

            // Capture frames at 10 FPS
            captureInterval = setInterval(() => {
                if (isStreaming && ws && ws.readyState === WebSocket.OPEN) {
                    captureFrame();
                }
            }, 100); // 10 FPS

            // Start audio capture if enabled
            if (micEnabled) {
                startAudioCapture();
            }
        }

        // Stop streaming
        function stopStreaming() {
            isStreaming = false;
            startBtn.textContent = 'Start';
            startBtn.classList.remove('recording');

            if (captureInterval) {
                clearInterval(captureInterval);
                captureInterval = null;
            }

            if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                mediaRecorder.stop();
            }
        }

        // Capture single frame
        function captureFrame() {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            ctx.drawImage(video, 0, 0);

            canvas.toBlob((blob) => {
                if (blob && ws && ws.readyState === WebSocket.OPEN) {
                    // Prepend 0x01 to indicate frame data
                    const reader = new FileReader();
                    reader.onload = () => {
                        const arrayBuffer = reader.result;
                        const data = new Uint8Array(arrayBuffer.byteLength + 1);
                        data[0] = 0x01; // Frame marker
                        data.set(new Uint8Array(arrayBuffer), 1);
                        ws.send(data.buffer);
                        frameCounter++;
                        frameCount.textContent = frameCounter;
                    };
                    reader.readAsArrayBuffer(blob);
                }
            }, 'image/jpeg', 0.8);
        }

        // Start audio capture
        async function startAudioCapture() {
            try {
                audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(audioStream);

                mediaRecorder.ondataavailable = (event) => {
                    if (event.data.size > 0 && ws && ws.readyState === WebSocket.OPEN) {
                        // Prepend 0x02 to indicate audio data
                        const reader = new FileReader();
                        reader.onload = () => {
                            const arrayBuffer = reader.result;
                            const data = new Uint8Array(arrayBuffer.byteLength + 1);
                            data[0] = 0x02; // Audio marker
                            data.set(new Uint8Array(arrayBuffer), 1);
                            ws.send(data.buffer);
                        };
                        reader.readAsArrayBuffer(event.data);
                    }
                };

                // Send audio chunks every 100ms
                mediaRecorder.start(100);
            } catch (error) {
                console.error('Error accessing microphone:', error);
                alert('Could not access microphone. Audio will be disabled.');
                micEnabled = false;
                updateMicButton();
            }
        }

        // Flip camera
        async function flipCamera() {
            facingMode = facingMode === 'environment' ? 'user' : 'environment';
            if (stream) {
                stream.getTracks().forEach(track => track.stop());
            }
            await initCamera();
        }

        // Toggle microphone
        function toggleMic() {
            micEnabled = !micEnabled;
            updateMicButton();

            if (micEnabled && isStreaming) {
                startAudioCapture();
            } else if (!micEnabled && mediaRecorder) {
                if (mediaRecorder.state !== 'inactive') {
                    mediaRecorder.stop();
                }
                if (audioStream) {
                    audioStream.getTracks().forEach(track => track.stop());
                    audioStream = null;
                }
            }
        }

        function updateMicButton() {
            if (micEnabled) {
                micBtn.classList.add('active');
                audioStatus.textContent = 'Enabled';
                audioStatus.className = 'status-value connected';
            } else {
                micBtn.classList.remove('active');
                audioStatus.textContent = 'Disabled';
                audioStatus.className = 'status-value';
            }
        }

        // Button handlers
        startBtn.addEventListener('click', () => {
            if (isStreaming) {
                stopStreaming();
            } else {
                startStreaming();
            }
        });

        flipBtn.addEventListener('click', flipCamera);
        micBtn.addEventListener('click', toggleMic);

        // Initialize
        connectWebSocket();
        initCamera();
    </script>
</body>
</html>
"""


class CameraBridge:
    """WebSocket server that receives camera frames from a phone."""

    def __init__(self, port: int = 8443):
        self.port = port
        self.pairing = PairingManager()
        self.frame_queue = FrameQueue(max_size=30)
        # Use bounded deque to prevent unbounded memory growth (~1000 chunks ≈ 60s at 16kHz)
        self.audio_buffer: deque = deque(maxlen=1000)
        self.is_running = False
        self.is_phone_connected = False
        self._server_task = None
        self._server = None

    def generate_pairing_qr(self, host_ip: str) -> dict[str, Any]:
        """Generate QR code for pairing. Returns {url, qr_ascii, expires_in_seconds, instructions}."""
        self.pairing.generate_token()
        url = self.pairing.get_pairing_url(host_ip, self.port)

        # Generate ASCII QR code
        qr = qrcode.QRCode(box_size=1, border=1)
        qr.add_data(url)
        qr.make(fit=True)

        # Get ASCII representation
        buf = io.StringIO()
        qr.print_ascii(out=buf)

        return {
            "url": url,
            "qr_ascii": buf.getvalue(),
            "expires_in_seconds": self.pairing.expiry_seconds,
            "instructions": "Scan this QR code with your iPhone camera."
        }

    async def _handle_http(self, path: str, request_headers) -> tuple[int, list, bytes]:
        """Handle HTTP requests (serve the phone app HTML)."""
        if path == "/" or path.startswith("/?"):
            return (200, [("Content-Type", "text/html")], PHONE_APP_HTML.encode())
        else:
            return (404, [], b"Not Found")

    async def _handle_websocket(self, websocket: ServerConnection):
        """Handle WebSocket connection from phone."""
        try:
            # First message should be the pairing token
            token_msg = await websocket.recv()

            if isinstance(token_msg, bytes):
                token = token_msg.decode('utf-8')
            else:
                token = token_msg

            if not self.pairing.validate_token(token):
                await websocket.close(1008, "Invalid or expired token")
                return

            self.is_phone_connected = True
            print(f"Phone connected from {websocket.remote_address}")

            # Receive frames and audio
            async for message in websocket:
                if isinstance(message, bytes):
                    if len(message) < 1:
                        continue

                    marker = message[0]
                    data = message[1:]

                    if marker == 0x01:  # Frame data
                        timestamp = time.time()
                        self.frame_queue.push(data, timestamp)
                    elif marker == 0x02:  # Audio data
                        self.audio_buffer.append(data)

        except Exception as e:
            print(f"WebSocket error: {e}")
        finally:
            self.is_phone_connected = False
            print("Phone disconnected")

    async def start(self, host_ip: str, certfile: str, keyfile: str):
        """Start the HTTPS + WebSocket server."""
        # Create SSL context
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(certfile, keyfile)

        # Start WebSocket server with max frame size limit to prevent oversized frames
        self._server = await serve(
            self._handle_websocket,
            host_ip,
            self.port,
            ssl=ssl_context,
            process_request=self._handle_http,
            max_size=1048576,  # 1MB max frame size
        )

        self.is_running = True
        print(f"Camera bridge server started on https://{host_ip}:{self.port}")

    async def stop(self):
        """Stop the server and clear all buffers."""
        self.is_running = False

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        self.frame_queue.clear()
        self.audio_buffer.clear()
        self.is_phone_connected = False
        print("Camera bridge server stopped")
