class TCPProtocol:
    def __init__(self, torrent):
        self.torrent = torrent

    def send_message(self, message: bytes):
        pass

    def receive_message(self) -> bytes:
        pass
