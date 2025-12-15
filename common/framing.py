# common/framing.py
import struct, json, socket

MAX_LEN = 65536

def send_raw(sock: socket.socket, payload: bytes):
    if len(payload) > MAX_LEN:
        raise ValueError("payload too large")
    hdr = struct.pack('!I', len(payload))
    _sendall(sock, hdr)
    _sendall(sock, payload)

def send_json(sock: socket.socket, obj: dict):
    data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    send_raw(sock, data)

def recv_raw(sock: socket.socket) -> bytes | None:
    hdr = _recvn(sock, 4)
    if not hdr:
        return None
    (n,) = struct.unpack('!I', hdr)
    if n <= 0 or n > MAX_LEN:
        return None
    body = _recvn(sock, n)
    return body

def recv_json(sock: socket.socket) -> dict | None:
    body = recv_raw(sock)
    if not body: return None
    try:
        return json.loads(body.decode('utf-8'))
    except Exception:
        return None

def _sendall(sock: socket.socket, data: bytes):
    view = memoryview(data)
    while len(view):
        n = sock.send(view)
        if n <= 0: raise ConnectionError("send error")
        view = view[n:]

def _recvn(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        k = sock.recv_into(view[got:])
        if k == 0: return None
        got += k
    return bytes(buf)
